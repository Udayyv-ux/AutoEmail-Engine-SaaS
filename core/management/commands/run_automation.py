import os
import time
import logging
import traceback
from typing import List, Dict, Optional
from email.utils import formataddr
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

# Third-party
import gspread
from groq import Groq
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from google.oauth2.credentials import Credentials

# Models
from core.models import ClientProfile, EmailTemplate
from allauth.socialaccount.models import SocialToken

# =====================================================================
# 1. ENTERPRISE CONFIGURATION & LOGGING
# =====================================================================
logger = logging.getLogger("autoemail_engine")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

GROQ_API_TOKEN = os.environ.get('GROQ_API_KEY', '').strip()

# =====================================================================
# 2. DOMAIN SERVICES (Decoupled Logic)
# =====================================================================

class AIEngine:
    """Handles all interactions with the LLM API."""
    def __init__(self, api_key: str):
        self.client = Groq(api_key=api_key, timeout=15.0)
        self.model = "llama-3.1-8b-instant"

    def classify_intent(self, query: str, valid_categories: List[str]) -> Optional[str]:
        if not valid_categories:
            return None
            
        categories_str = ", ".join([f"'{c}'" for c in valid_categories])
        prompt = (
            f"You are an email routing bot. Valid categories: [{categories_str}]. "
            f"Analyze inquiry: '{query}'. Match to closest category. Respond ONLY with the exact category name. Do not add punctuation."
        )
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.0
            )
            return response.choices[0].message.content.strip(".'\" \n")
        except Exception as e:
            logger.error(f"AI Inference failed: {str(e)}")
            return None


class EmailSender:
    """
    Abstracts email sending. 
    NOTE: To permanently fix Railway blocking, transition this to use 
    an HTTP API (like Resend) or the Google Gmail API (Port 443).
    """
    def __init__(self, client_profile):
        self.client = client_profile
        self.server = None

    def connect(self):
        import smtplib
        # Fallback to SMTP relay ports like 2525 if standard ports are blocked by PaaS
        # Better yet: Swap this entire method out for an HTTP request to Resend/SendGrid.
        self.server = smtplib.SMTP('smtp.gmail.com', 587) 
        self.server.starttls()
        self.server.login(self.client.sender_email, self.client.gmail_app_password)

    def send(self, to_email: str, name: str, template: EmailTemplate):
        msg = MIMEMultipart('related')
        msg['From'] = formataddr((self.client.business_name, self.client.sender_email))
        msg['To'] = to_email
        msg['Subject'] = template.subject

        compiled_html = template.html_body.replace("{first_name}", name).replace("{{Name}}", name)

        msg_alternative = MIMEMultipart('alternative')
        msg.attach(msg_alternative)

        if template.image and os.path.exists(template.image.path):
            image_html = (
                '<div style="text-align:center;margin-bottom:20px;">'
                '<img src="cid:banner_img" style="max-width:100%;border-radius:8px;'
                'box-shadow:0 4px 10px rgba(0,0,0,0.1);"></div>'
            )
            compiled_html = image_html + compiled_html
            msg_alternative.attach(MIMEText(compiled_html, 'html'))
            
            with open(template.image.path, 'rb') as img_file:
                img_mime = MIMEImage(img_file.read())
                img_mime.add_header('Content-ID', '<banner_img>')
                img_mime.add_header('Content-Disposition', 'inline')
                msg.attach(img_mime)
        else:
            msg_alternative.attach(MIMEText(compiled_html, 'html'))

        self.server.send_message(msg)

    def disconnect(self):
        if self.server:
            self.server.quit()


class PipelineProcessor:
    """Orchestrates the data flow for a single client."""
    def __init__(self, client: ClientProfile):
        self.client = client
        self.ai = AIEngine(GROQ_API_TOKEN)
        self.email_sender = EmailSender(client)
        
    def execute(self):
        logger.info(f"[{self.client.business_name}] Booting Pipeline...")
        
        # 1. Validation
        if not all([self.client.sender_email, self.client.gmail_app_password, self.client.google_sheet_link]):
            logger.warning(f"[{self.client.business_name}] Missing credentials. Skipping.")
            return

        google_token = SocialToken.objects.filter(account__user=self.client.user, account__provider='google').first()
        if not google_token:
            logger.warning(f"[{self.client.business_name}] No Google Token found. Skipping.")
            return

        templates = list(EmailTemplate.objects.filter(client=self.client))
        if not templates:
            logger.warning(f"[{self.client.business_name}] No templates deployed. Skipping.")
            return
            
        template_map = {t.project_id.lower(): t for t in templates}

        # 2. Extract Data
        try:
            creds = Credentials(
                token=google_token.token,
                refresh_token=google_token.token_secret,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=settings.SOCIALACCOUNT_PROVIDERS['google']['APP']['client_id'],
                client_secret=settings.SOCIALACCOUNT_PROVIDERS['google']['APP']['secret']
            )
            g_client = gspread.authorize(creds)
            sheet_id = self.client.google_sheet_link.split('/d/')[1].split('/')[0]
            sheet = g_client.open_by_key(sheet_id).sheet1
            
            records = sheet.get_all_records()
            headers = sheet.row_values(1)
            status_col_index = headers.index(self.client.col_status) + 1
        except Exception as e:
            logger.error(f"[{self.client.business_name}] Sheet Error: {str(e)}")
            return

        # 3. Process Logic
        try:
            self.email_sender.connect()
        except Exception as e:
            logger.error(f"[{self.client.business_name}] Email Auth Failed: {str(e)}")
            return

        cells_to_update = []
        emails_sent = 0

        for index, row in enumerate(records):
            actual_row = index + 2
            status = str(row.get(self.client.col_status, "")).strip().upper()
            email = str(row.get(self.client.col_email, "")).strip()
            name = str(row.get(self.client.col_name, "Customer"))
            query = str(row.get(self.client.col_query, "")).strip()

            if status == "SENT" or "@" not in email or len(query) < 2:
                continue

            logger.info(f"[{self.client.business_name}] Processing Row {actual_row} -> {email}")

            try:
                # AI Inference
                matched_category = self.ai.classify_intent(query, list(template_map.keys()))
                if not matched_category or matched_category.lower() not in template_map:
                    logger.warning(f"[{self.client.business_name}] Hallucination/No Match: {matched_category}. Skipping.")
                    continue
                
                template = template_map[matched_category.lower()]

                # Send Email
                self.email_sender.send(email, name, template)
                emails_sent += 1
                
                # Queue batch update
                cells_to_update.append(gspread.Cell(row=actual_row, col=status_col_index, value="SENT"))

            except Exception as e:
                logger.error(f"[{self.client.business_name}] Failed row {actual_row}: {str(e)}")

        self.email_sender.disconnect()

        # 4. Batch DB & Sheet Updates (Prevents 429 Rate Limits)
        if cells_to_update:
            sheet.update_cells(cells_to_update)
            logger.info(f"[{self.client.business_name}] Batch updated {len(cells_to_update)} rows in Google Sheets.")
            
        self.client.emails_sent_count += emails_sent
        self.client.last_scanned = timezone.now()
        self.client.save(update_fields=['emails_sent_count', 'last_scanned'])

# =====================================================================
# 3. COMMAND ENTRY POINT
# =====================================================================

class Command(BaseCommand):
    help = "Processes the email engine pipeline for all active clients."

    def handle(self, *args, **options):
        logger.info("Initializing AutoEmail Engine...")

        if not GROQ_API_TOKEN:
            logger.error("FATAL: GROQ_API_KEY missing.")
            return

        # ENTERPRISE NOTE: 
        # Remove the `while True` loop if running in production. 
        # This script should be executed every 5 minutes by a Cron job or Celery Beat.
        active_nodes = ClientProfile.objects.filter(is_engine_active=True)
        
        for client in active_nodes:
            processor = PipelineProcessor(client)
            try:
                processor.execute()
            except Exception as e:
                logger.error(f"Critical fault in {client.business_name}: {str(e)}")
                traceback.print_exc()
        
        logger.info("Engine cycle complete.")
import os
import time
import random
import smtplib
import ssl
import logging
import traceback
from typing import List, Optional
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
# 1. SETUP & LOGGING
# =====================================================================
logger = logging.getLogger("autoemail_engine")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

GROQ_API_TOKEN = os.environ.get('GROQ_API_KEY', '').strip()

# =====================================================================
# 2. CORE SERVICES
# =====================================================================

class EmailSender:
    """Handles SMTP connections with Triple-Port Fallback."""
    def __init__(self, client_profile):
        self.client = client_profile
        self.server = None

    def connect(self):
        # 1. 465 (SSL - Standard), 2. 587 (STARTTLS), 3. 2525 (Alternative relay)
        connection_strategies = [
            (465, 'ssl'),
            (587, 'starttls'),
            (2525, 'starttls')
        ]
        
        last_error = None
        
        for port, strategy in connection_strategies:
            try:
                logger.info(f"[{self.client.business_name}] Attempting SMTP on port {port} ({strategy})...")
                if strategy == 'ssl':
                    context = ssl.create_default_context()
                    self.server = smtplib.SMTP_SSL('smtp.gmail.com', port, context=context, timeout=15)
                else:
                    self.server = smtplib.SMTP('smtp.gmail.com', port, timeout=15)
                    self.server.starttls()
                
                self.server.login(self.client.sender_email, self.client.gmail_app_password)
                logger.info(f"[{self.client.business_name}] ✅ SMTP Auth SUCCESS on port {port}!")
                return 
                
            except Exception as e:
                logger.warning(f"[{self.client.business_name}] ⚠️ Port {port} failed: {str(e)}")
                last_error = e
                if self.server:
                    try:
                        self.server.quit()
                    except:
                        pass
                    self.server = None
        
        raise Exception(f"All SMTP connection attempts blocked. Last error: {str(last_error)}")

    def send(self, to_email: str, name: str, template: EmailTemplate):
        if not self.server:
            raise Exception("SMTP Server not connected.")
            
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
            try:
                self.server.quit()
            except:
                pass


class PipelineProcessor:
    """Orchestrates data flow for a single client (Runs Sequentially)."""
    def __init__(self, client: ClientProfile):
        self.client = client
        self.email_sender = EmailSender(client)
        
    def execute(self):
        logger.info(f"\n--- [ENGINE NODE] Booting Pipeline: {self.client.business_name} ---")
        
        if not all([self.client.sender_email, self.client.gmail_app_password, self.client.google_sheet_link]):
            logger.warning(f"[{self.client.business_name}] Missing credentials. Skipping.")
            return

        google_token = SocialToken.objects.filter(account__user=self.client.user, account__provider='google').first()
        if not google_token:
            logger.warning(f"[{self.client.business_name}] No Google OAuth Token. Skipping.")
            return

        templates = list(EmailTemplate.objects.filter(client=self.client))
        if not templates:
            logger.warning(f"[{self.client.business_name}] No templates deployed. Skipping.")
            return
            
        template_map = {t.project_id.lower(): t for t in templates}

        # 1. Connect to Sheets
        logger.info(f"[{self.client.business_name}] Connecting to Google Sheets...")
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
            
            if self.client.col_status not in headers:
                logger.warning(f"[{self.client.business_name}] Status column '{self.client.col_status}' not found in sheet.")
                return
            status_col_index = headers.index(self.client.col_status) + 1
            
        except Exception as e:
            logger.error(f"[{self.client.business_name}] Sheet Connection Error: {str(e)}")
            return

        # 2. Identify rows that need processing
        rows_to_process = []
        for index, row in enumerate(records):
            status = str(row.get(self.client.col_status, "")).strip().upper()
            email = str(row.get(self.client.col_email, "")).strip()
            query = str(row.get(self.client.col_query, "")).strip()
            
            if status != "SENT" and "@" in email and len(query) >= 2:
                rows_to_process.append((index, row))

        if not rows_to_process:
            logger.info(f"[{self.client.business_name}] No unread/new leads found.")
            return

        # 3. Connect to AI & SMTP
        ai_client = Groq(api_key=GROQ_API_TOKEN, timeout=15.0)
        try:
            self.email_sender.connect()
        except Exception as e:
            logger.error(f"[{self.client.business_name}] Email Auth Failed: {str(e)}")
            return

        cells_to_update = []
        emails_sent = 0
        categories_str = ", ".join([f"'{c}'" for c in template_map.keys()])

        # 4. Blast the Emails
        for index, row in rows_to_process:
            actual_row = index + 2
            email = str(row.get(self.client.col_email, "")).strip()
            name = str(row.get(self.client.col_name, "Customer"))
            query = str(row.get(self.client.col_query, "")).strip()

            logger.info(f"[{self.client.business_name}] Processing Row {actual_row} -> {email}")

            try:
                # Ask Groq AI
                prompt = (
                    f"You are an email routing bot. Valid categories: [{categories_str}]. "
                    f"Analyze inquiry: '{query}'. Match to closest category. Respond ONLY with the exact category name."
                )
                response = ai_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model="llama-3.1-8b-instant",
                    temperature=0.0
                )
                matched_category = response.choices[0].message.content.strip(".'\" \n").lower()

                if matched_category not in template_map:
                    logger.warning(f"[{self.client.business_name}] AI Hallucination: '{matched_category}'. Skipping.")
                    continue
                
                template = template_map[matched_category]

                # Send Email
                self.email_sender.send(email, name, template)
                emails_sent += 1
                logger.info(f"[{self.client.business_name}] ✅ Blast successful to {email}")
                
                cells_to_update.append(gspread.Cell(row=actual_row, col=status_col_index, value="SENT"))

                # 5. Smart Throttling
                sleep_time = random.uniform(2.5, 4.5)
                time.sleep(sleep_time)

            except Exception as e:
                logger.error(f"[{self.client.business_name}] Failed sending to {email}: {str(e)}")

        self.email_sender.disconnect()

        # 6. Save State
        if cells_to_update:
            try:
                sheet.update_cells(cells_to_update)
                logger.info(f"[{self.client.business_name}] Marked {len(cells_to_update)} rows as SENT in Google Sheets.")
            except Exception as e:
                logger.error(f"[{self.client.business_name}] Failed to update Google Sheet: {str(e)}")
            
        self.client.emails_sent_count += emails_sent
        self.client.last_scanned = timezone.now()
        self.client.save(update_fields=['emails_sent_count', 'last_scanned'])


# =====================================================================
# 3. COMMAND ENTRY POINT (Cron Safe)
# =====================================================================

class Command(BaseCommand):
    help = "Processes the email engine pipeline safely for Cron execution."

    def handle(self, *args, **options):
        logger.info("Initializing AutoEmail Engine Matrix...")

        if not GROQ_API_TOKEN:
            logger.error("FATAL: GROQ_API_KEY missing from environment variables.")
            return

        active_nodes = ClientProfile.objects.filter(is_engine_active=True)
        
        if not active_nodes.exists():
            logger.info("No active clients detected.")
            return
        
        # Sequentially process each node to prevent Google Sheet Rate Limits & Deadlocks
        for client in active_nodes:
            processor = PipelineProcessor(client)
            try:
                processor.execute()
            except Exception as e:
                logger.error(f"Critical fault in {client.business_name}: {str(e)}")
                traceback.print_exc()
        
        logger.info("Matrix Cycle Complete. Shutting down until next Cron trigger.")
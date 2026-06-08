import time
import random
import smtplib
import re
import gspread
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from concurrent.futures import ThreadPoolExecutor
from groq import Groq

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from core.models import ClientProfile, EmailTemplate

from allauth.socialaccount.models import SocialToken
from google.oauth2.credentials import Credentials

GROQ_API_TOKEN = ""

def extract_sheet_id(url):
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    return match.group(1) if match else None

def process_client_pipeline(client):
    print(f"\n--- [ENGINE NODE] Scanning: {client.business_name} ---")
    
    if not client.gmail_app_password or not client.sender_email or not client.google_sheet_link:
        return

    google_token = SocialToken.objects.filter(account__user=client.user, account__provider='google').first()
    if not google_token:
        return
        
    creds = Credentials(
        token=google_token.token,
        refresh_token=google_token.token_secret,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=settings.SOCIALACCOUNT_PROVIDERS['google']['APP']['client_id'],
        client_secret=settings.SOCIALACCOUNT_PROVIDERS['google']['APP']['secret']
    )

    try:
        # Fetch Available Templates First
        available_templates = list(EmailTemplate.objects.filter(client=client).values_list('project_id', flat=True))
        if not available_templates:
            print(f"WARNING: No templates deployed. Engine cannot route emails.")
            return
            
        template_list_string = ", ".join([f"'{t}'" for t in available_templates])

        G_CLIENT = gspread.authorize(creds)
        client.last_scanned = timezone.now()
        client.save(update_fields=['last_scanned'])

        sheet_id = extract_sheet_id(client.google_sheet_link)
        sheet = G_CLIENT.open_by_key(sheet_id).sheet1
        all_records = sheet.get_all_records()
        headers = sheet.row_values(1)
        
        try:
            status_col_index = headers.index(client.col_status) + 1 
        except ValueError:
            print(f"ERROR: Cannot find column '{client.col_status}'")
            return
            
        ai_client = Groq(api_key=GROQ_API_TOKEN)

        for index, row in enumerate(all_records):
            actual_sheet_row = index + 2 
            status_val = str(row.get(client.col_status, "")).strip().upper()
            email = str(row.get(client.col_email, "")).strip()
            name = str(row.get(client.col_name, "Customer"))
            query = str(row.get(client.col_query, "")).strip()
            
            # --- THE 3 STRICT SAFEGUARDS ---
            if status_val == "SENT": 
                continue # Ignore already sent
                
            if not email or "@" not in email: 
                continue # Ignore invalid emails
                
            if not query or len(query) < 2:
                # NEW KILLSWITCH: If the inquiry is blank, skip the row entirely!
                print(f"Skipping Row {actual_sheet_row}: Email found, but Inquiry is empty.")
                continue

            print(f"Found Unread Lead -> Name: {name}, Email: {email}, Query: {query}")

            try:
                # AI Classification
                prompt = (
                    f"You are an email routing bot. Here are the ONLY valid categories: [{template_list_string}]. "
                    f"Analyze this user inquiry: '{query}'. "
                    f"Match the inquiry to the absolute closest category from the list above. "
                    f"Respond ONLY with the exact category name from the list. Do not add punctuation or explanation."
                )
                
                ai_inference = ai_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model="llama-3.1-8b-instant",
                    temperature=0.0
                )
                
                extracted_token = ai_inference.choices[0].message.content.strip(".'\" \n")
                print(f"AI Selected Category: '{extracted_token}'")

                active_template = EmailTemplate.objects.get(client=client, project_id__iexact=extracted_token)
                compiled_html = active_template.html_body.replace("{first_name}", name)
                
                message_payload = MIMEMultipart('alternative')
                message_payload['Subject'] = active_template.subject
                message_payload['From'] = client.sender_email
                message_payload['To'] = email
                message_payload.attach(MIMEText(compiled_html, 'html'))
                
                time.sleep(random.uniform(1.0, 2.5))
                
                with smtplib.SMTP_SSL('smtp.gmail.com', 465) as secure_socket:
                    secure_socket.login(client.sender_email, client.gmail_app_password)
                    secure_socket.sendmail(client.sender_email, email, message_payload.as_string())
                
                sheet.update_cell(actual_sheet_row, status_col_index, "SENT")
                client.emails_sent_count += 1
                client.save(update_fields=['emails_sent_count'])
                print(f"SUCCESS: Email Dispatched to {email}")

            except EmailTemplate.DoesNotExist:
                print(f"WARNING: AI failed to match strictly. It returned '{extracted_token}'.")
            
    except Exception as e:
        print(f"CRITICAL FAULT for '{client.business_name}': {repr(e)}")

class Command(BaseCommand):
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("AutoEmail Engine Matrix Online. Strict AI Routing Active..."))
        while True:
            active_nodes = ClientProfile.objects.filter(is_engine_active=True)
            if active_nodes.exists():
                with ThreadPoolExecutor(max_workers=5) as scheduler:
                    scheduler.map(process_client_pipeline, active_nodes)
            time.sleep(30)
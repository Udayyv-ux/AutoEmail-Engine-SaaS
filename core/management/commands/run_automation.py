import time
import random
import smtplib
import re
import os
import traceback
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

# Load API Key from Environment
GROQ_API_TOKEN = os.environ.get('GROQ_API_KEY')

def extract_sheet_id(url):
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    return match.group(1) if match else None

def process_client_pipeline(client):
    print(f"\n--- [ENGINE NODE] Scanning: {client.business_name} ---")
    
    # 1. Validation Checks
    if not client.gmail_app_password or not client.sender_email or not client.google_sheet_link:
        print(f"Skipping {client.business_name}: Missing credentials.")
        return

    google_token = SocialToken.objects.filter(account__user=client.user, account__provider='google').first()
    if not google_token:
        print(f"Skipping {client.business_name}: No Google Token found.")
        return
        
    try:
        creds = Credentials(
            token=google_token.token,
            refresh_token=google_token.token_secret,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=settings.SOCIALACCOUNT_PROVIDERS['google']['APP']['client_id'],
            client_secret=settings.SOCIALACCOUNT_PROVIDERS['google']['APP']['secret']
        )

        # 2. Setup Templates and API
        available_templates = list(EmailTemplate.objects.filter(client=client).values_list('project_id', flat=True))
        if not available_templates:
            print(f"WARNING: No templates for {client.business_name}.")
            return
            
        template_list_string = ", ".join([f"'{t}'" for t in available_templates])
        G_CLIENT = gspread.authorize(creds)
        ai_client = Groq(api_key=GROQ_API_TOKEN)

        # 3. Access Sheet
        sheet_id = extract_sheet_id(client.google_sheet_link)
        sheet = G_CLIENT.open_by_key(sheet_id).sheet1
        all_records = sheet.get_all_records()
        headers = sheet.row_values(1)
        
        try:
            status_col_index = headers.index(client.col_status) + 1 
        except ValueError:
            print(f"ERROR: Cannot find column '{client.col_status}' in sheet.")
            return

        # 4. Process Rows
        for index, row in enumerate(all_records):
            actual_sheet_row = index + 2 
            status_val = str(row.get(client.col_status, "")).strip().upper()
            email = str(row.get(client.col_email, "")).strip()
            name = str(row.get(client.col_name, "Customer"))
            query = str(row.get(client.col_query, "")).strip()
            
            if status_val == "SENT" or not email or "@" not in email or not query or len(query) < 2:
                continue

            print(f"Processing Lead -> Name: {name}, Email: {email}")

            try:
                # AI Classification
                prompt = (
                    f"You are an email routing bot. Valid categories: [{template_list_string}]. "
                    f"Analyze inquiry: '{query}'. Match to closest category. Respond ONLY with category name."
                )
                
                ai_inference = ai_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model="llama-3.1-8b-instant",
                    temperature=0.0
                )
                
                extracted_token = ai_inference.choices[0].message.content.strip(".'\" \n")
                active_template = EmailTemplate.objects.get(client=client, project_id__iexact=extracted_token)
                
                # Send Email
                compiled_html = active_template.html_body.replace("{first_name}", name)
                message_payload = MIMEMultipart('alternative')
                message_payload['Subject'] = active_template.subject
                message_payload['From'] = client.sender_email
                message_payload['To'] = email
                message_payload.attach(MIMEText(compiled_html, 'html'))
                
                time.sleep(random.uniform(1.0, 2.0))
                
                with smtplib.SMTP_SSL('smtp.gmail.com', 465) as secure_socket:
                    secure_socket.login(client.sender_email, client.gmail_app_password)
                    secure_socket.sendmail(client.sender_email, email, message_payload.as_string())
                
                sheet.update_cell(actual_sheet_row, status_col_index, "SENT")
                client.emails_sent_count += 1
                client.save(update_fields=['emails_sent_count'])
                print(f"SUCCESS: Email sent to {email}")

            except Exception as inner_e:
                print(f"FAILED to process lead for {client.business_name}: {str(inner_e)}")

    except Exception:
        print(f"CRITICAL FAULT for '{client.business_name}':")
        traceback.print_exc() # This will show you exactly what is breaking

class Command(BaseCommand):
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("AutoEmail Engine Matrix Online. Strict AI Routing Active..."))
        while True:
            active_nodes = ClientProfile.objects.filter(is_engine_active=True)
            if active_nodes.exists():
                with ThreadPoolExecutor(max_workers=5) as scheduler:
                    scheduler.map(process_client_pipeline, active_nodes)
            time.sleep(30)
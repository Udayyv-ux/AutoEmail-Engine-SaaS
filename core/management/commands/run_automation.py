import os
import time
import random
import smtplib
import socket
import re
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

# --- 1. STRICT ENVIRONMENT LOADING ---
GROQ_API_TOKEN = os.environ.get('GROQ_API_KEY', '').strip()

def check_network():
    """Verifies the Railway container actually has internet access."""
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3.0)
        return True
    except OSError:
        return False

def extract_sheet_id(url):
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    return match.group(1) if match else None

def process_client_pipeline(client):
    try:
        print(f"\n--- [ENGINE NODE] Booting Pipeline: {client.business_name} ---")
        
        # Validation Checks
        if not client.gmail_app_password or not client.sender_email or not client.google_sheet_link:
            print(f"[{client.business_name}] SKIPPED: Missing Profile Credentials.")
            return

        google_token = SocialToken.objects.filter(account__user=client.user, account__provider='google').first()
        if not google_token:
            print(f"[{client.business_name}] SKIPPED: No Google Token found in database.")
            return

        creds = Credentials(
            token=google_token.token,
            refresh_token=google_token.token_secret,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=settings.SOCIALACCOUNT_PROVIDERS['google']['APP']['client_id'],
            client_secret=settings.SOCIALACCOUNT_PROVIDERS['google']['APP']['secret']
        )

        # Fetch Templates
        available_templates = list(EmailTemplate.objects.filter(client=client).values_list('project_id', flat=True))
        if not available_templates:
            print(f"[{client.business_name}] WARNING: No templates deployed.")
            return
            
        template_list_string = ", ".join([f"'{t}'" for t in available_templates])
        
        print(f"[{client.business_name}] Connecting to Google Sheets...")
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
            print(f"[{client.business_name}] ERROR: Column '{client.col_status}' not found in sheet headers.")
            return

        # Initialize Groq with a strict 15-second timeout to prevent infinite hanging
        ai_client = Groq(api_key=GROQ_API_TOKEN, timeout=15.0)

        # Process Rows
        for index, row in enumerate(all_records):
            actual_sheet_row = index + 2 
            status_val = str(row.get(client.col_status, "")).strip().upper()
            email = str(row.get(client.col_email, "")).strip()
            name = str(row.get(client.col_name, "Customer"))
            query = str(row.get(client.col_query, "")).strip()
            
            # Strict Filtering
            if status_val == "SENT" or not email or "@" not in email or not query or len(query) < 2:
                continue

            print(f"\n[{client.business_name}] Processing Lead -> Row: {actual_sheet_row} | Name: {name} | Email: {email}")

            try:
                # STEP 1: AI Routing
                print(f"  -> Step 1/4: Requesting AI routing from Groq...")
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
                print(f"  -> Step 2/4: AI matched to category '{extracted_token}'. Compiling Email...")

                # STEP 2: Compile Email
                try:
                    active_template = EmailTemplate.objects.get(client=client, project_id__iexact=extracted_token)
                except EmailTemplate.DoesNotExist:
                    print(f"  -> FAILURE: AI hallucinated a category '{extracted_token}' not in list. Skipping row.")
                    continue

                compiled_html = active_template.html_body.replace("{first_name}", name)
                message_payload = MIMEMultipart('alternative')
                message_payload['Subject'] = active_template.subject
                message_payload['From'] = client.sender_email
                message_payload['To'] = email
                message_payload.attach(MIMEText(compiled_html, 'html'))
                
                # STEP 3: SMTP Send
                print(f"  -> Step 3/4: Connecting to Gmail SMTP servers...")
                time.sleep(random.uniform(1.0, 2.0))
                
                # Strict 15-second timeout on SMTP to prevent Errno 101 hanging
                with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15.0) as secure_socket:
                    secure_socket.login(client.sender_email, client.gmail_app_password)
                    secure_socket.sendmail(client.sender_email, email, message_payload.as_string())
                
                # STEP 4: Google Sheet Update
                print(f"  -> Step 4/4: Email dispatched. Updating Google Sheet...")
                sheet.update_cell(actual_sheet_row, status_col_index, "SENT")
                
                # Update DB Stats
                client.emails_sent_count += 1
                client.save(update_fields=['emails_sent_count'])
                
                print(f"  -> SUCCESS: Lead fully processed.")

            except Exception as row_error:
                # This catches errors specific to this exact lead (like SMTP timeouts) so the engine can keep going
                print(f"  -> ROW FAILURE for {email}: {str(row_error)}")
                traceback.print_exc()

    except Exception as pipeline_error:
        # This catches massive pipeline failures so the thread doesn't die silently
        print(f"CRITICAL PIPELINE FAULT for '{client.business_name}':")
        traceback.print_exc()


class Command(BaseCommand):
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("AutoEmail Engine Matrix Online. Strict AI Routing Active..."))
        
        if not GROQ_API_TOKEN:
            self.stdout.write(self.style.ERROR("FATAL: GROQ_API_KEY is completely missing from environment variables."))
            return

        while True:
            if not check_network():
                print("NETWORK WARNING: Container has lost internet access. Sleeping for 15 seconds to wait for Railway network recovery...")
                time.sleep(15)
                continue

            active_nodes = ClientProfile.objects.filter(is_engine_active=True)
            if active_nodes.exists():
                with ThreadPoolExecutor(max_workers=5) as scheduler:
                    scheduler.map(process_client_pipeline, active_nodes)
            
            time.sleep(30)
import os
import csv
import smtplib
import requests
import logging
from io import StringIO
from email.utils import formataddr
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

from .models import ClientProfile, SystemSettings, EmailLog

logger = logging.getLogger(__name__)

class EngineService:
    """
    Enterprise Domain Service: Handles all email processing independently of the web views.
    Can be safely called by Celery workers, Cron jobs, or Admin views.
    """
    @staticmethod
    def process_client(client: ClientProfile) -> str:
        if SystemSettings.load().is_engine_paused:
            return "GLOBAL KILL SWITCH IS ACTIVE. Engine halted."

        if not client.is_engine_active:
            return "Workspace Engine is OFF."

        if not client.sender_email or not client.gmail_app_password:
            return "Missing sender email or app password in portal configuration."

        sheet_url = client.google_sheet_link
        if not sheet_url or "/d/" not in sheet_url:
            return "Invalid Google Sheet Link."

        # Fetch CSV Data
        sheet_id = sheet_url.split('/d/')[1].split('/')[0]
        csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"

        try:
            response = requests.get(csv_url, timeout=10)
            if response.status_code != 200:
                return "Cannot read sheet. Ensure share setting is 'Anyone with the link'."
            response.encoding = 'utf-8'
            csv_data = list(csv.DictReader(StringIO(response.text)))
        except Exception as e:
            logger.error(f"Sheet read error for {client.public_id}: {str(e)}")
            return "Failed to fetch data from Google Sheets."

        # Fetch active template
        template = client.templates.filter(is_active=True).first()
        if not template:
            return "No active email templates exist."

        # --- THE RAILWAY DEPLOYMENT FIX ---
        # Cloud providers block port 587. We attempt 465 (SSL) first, fallback to 587.
        smtp_server = None
        try:
            smtp_server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15)
            smtp_server.login(client.sender_email, client.gmail_app_password)
        except Exception as ssl_err:
            logger.warning(f"Port 465 failed. Falling back to 587. Error: {ssl_err}")
            try:
                smtp_server = smtplib.SMTP('smtp.gmail.com', 587, timeout=15)
                smtp_server.starttls()
                smtp_server.login(client.sender_email, client.gmail_app_password)
            except Exception as e:
                logger.error(f"SMTP Auth Failed: {e}")
                return "SMTP Authentication Failed. Ensure App Password is correct and Ports are unlocked."

        sent_count = 0

        for row in csv_data:
            recipient = row.get(client.col_email, '').strip()
            name = row.get(client.col_name, 'There').strip()

            if not recipient or '@' not in recipient:
                continue

            # Prevent duplicate sending
            if EmailLog.objects.filter(client=client, recipient_email=recipient, status=EmailLog.DeliveryStatus.SUCCESS).exists():
                continue

            try:
                msg = MIMEMultipart('related')
                msg['From'] = formataddr((client.business_name, client.sender_email))
                msg['To'] = recipient
                msg['Subject'] = template.subject

                msg_alternative = MIMEMultipart('alternative')
                msg.attach(msg_alternative)

                body_content = template.html_body.replace('{{Name}}', name)

                if template.image and os.path.exists(template.image.path):
                    image_html = (
                        '<div style="text-align:center;margin-bottom:20px;">'
                        '<img src="cid:banner_img" style="max-width:100%;border-radius:8px;'
                        'box-shadow:0 4px 10px rgba(0,0,0,0.1);"></div>'
                    )
                    body_content = image_html + body_content
                    msg_alternative.attach(MIMEText(body_content, 'html'))
                    
                    try:
                        with open(template.image.path, 'rb') as img_file:
                            img_mime = MIMEImage(img_file.read())
                            img_mime.add_header('Content-ID', '<banner_img>')
                            img_mime.add_header('Content-Disposition', 'inline')
                            msg.attach(img_mime)
                    except Exception as img_e:
                        logger.error(f"Image attachment failed: {img_e}")
                else:
                    msg_alternative.attach(MIMEText(body_content, 'html'))

                smtp_server.send_message(msg)

                # Record success
                EmailLog.objects.create(
                    client=client, 
                    template=template, 
                    recipient_email=recipient, 
                    status=EmailLog.DeliveryStatus.SUCCESS
                )
                sent_count += 1

            except Exception as e:
                # Record failure
                EmailLog.objects.create(
                    client=client,
                    template=template,
                    recipient_email=recipient,
                    status=EmailLog.DeliveryStatus.FAILED,
                    error_message=str(e)[:250]
                )

        if smtp_server:
            smtp_server.quit()
            
        client.emails_sent_count += sent_count
        # Resetting time triggers the `last_scanned` index for faster DB queries
        client.save(update_fields=['emails_sent_count'])

        return f"Scan Complete. {sent_count} new emails delivered."
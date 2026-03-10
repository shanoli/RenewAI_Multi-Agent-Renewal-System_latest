"""
Standalone SendGrid email test script.
Configure credentials via environment variables or .env file before running.
"""
import os
import time
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "YOUR_SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("SENDGRID_FROM_EMAIL", "noreply@yourdomain.com")
TEST_RECIPIENT = os.getenv("SENDGRID_TEST_RECIPIENT", FROM_EMAIL)


def send_test_email():
    current_time = time.strftime("%Y-%m-%d %H:%M:%S")
    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=TEST_RECIPIENT,
        subject=f'Renewal System Test Email - {current_time}',
        plain_text_content=f'Hello! Email integration is working. Sent at: {current_time}'
    )

    sg = SendGridAPIClient(SENDGRID_API_KEY)
    response = sg.send(message)

    print("Status Code:", response.status_code)


send_test_email()
"""
Standalone WhatsApp test script.
Configure credentials via environment variables or .env file before running.
"""
import os
from twilio.rest import Client

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "YOUR_TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "YOUR_TWILIO_AUTH_TOKEN")
FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "whatsapp:+14155238886")
TO_NUMBER = os.getenv("TWILIO_TEST_NUMBER", "whatsapp:+91XXXXXXXXXX")

client = Client(ACCOUNT_SID, AUTH_TOKEN)

message = client.messages.create(
    body="""
Hi customer_name,

Your Jeevan Raksha Yojana policy policy_number
is due for renewal on due_date.

Annual Premium: ₹amount

Reply 1 to renew
Reply HUMAN to speak to advisor
""",
    from_=FROM_NUMBER,
    to=TO_NUMBER
)

print("Message SID:", message.sid)
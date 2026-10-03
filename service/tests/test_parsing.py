import email

from app.mail import parsing

HARD_BOUNCE = """\
From: Mail Delivery System <MAILER-DAEMON@mail.example.com>
To: outreach1@example.com
Subject: Undeliverable: quick question
Content-Type: multipart/report; report-type=delivery-status; boundary="b1"

--b1
Content-Type: text/plain

Your message could not be delivered.

--b1
Content-Type: message/delivery-status

Final-Recipient: rfc822; dana.okafor@northwind.example
Action: failed
Status: 5.1.1
Diagnostic-Code: smtp; 550 5.1.1 User unknown

--b1--
"""

SOFT_BOUNCE = """\
From: postmaster@mail.example.com
To: outreach1@example.com
Subject: Delivery Status Notification (Delay)
Content-Type: multipart/report; report-type=delivery-status; boundary="b2"

--b2
Content-Type: message/delivery-status

Final-Recipient: rfc822; sam.patel@brightline.example
Action: failed
Status: 4.2.2
Diagnostic-Code: smtp; 452 4.2.2 Mailbox full

--b2--
"""

OUT_OF_OFFICE = """\
From: Dana Okafor <dana@northwind.example>
To: outreach1@example.com
Subject: Automatic reply: quick question
Auto-Submitted: auto-replied
Content-Type: text/plain

I am out of the office until 5 October.
"""

HUMAN_REPLY = """\
From: Sam Patel <sam@brightline.example>
To: outreach1@example.com
In-Reply-To: <abc123@outreach.example>
References: <abc123@outreach.example>
Subject: Re: phishing tests
Content-Type: text/plain

Interesting - can you send over the benchmark you mentioned?
"""

OPT_OUT = """\
From: Riley Moreno <riley@cedarway.example>
To: outreach1@example.com
Subject: Re: training completion
Content-Type: text/plain

Please remove me from your list. Thanks.
"""


def _msg(raw):
    return email.message_from_string(raw)


def test_hard_bounce_detected_and_classified():
    m = _msg(HARD_BOUNCE)
    assert parsing.is_bounce(m) is True
    recipient, permanent = parsing.classify_bounce(m, parsing.extract_text(m))
    assert recipient == "dana.okafor@northwind.example"
    assert permanent is True


def test_soft_bounce_is_not_permanent():
    m = _msg(SOFT_BOUNCE)
    assert parsing.is_bounce(m) is True
    recipient, permanent = parsing.classify_bounce(m, parsing.extract_text(m))
    assert recipient == "sam.patel@brightline.example"
    assert permanent is False


def test_out_of_office_is_auto_reply_not_bounce():
    m = _msg(OUT_OF_OFFICE)
    assert parsing.is_auto_reply(m) is True
    assert parsing.is_bounce(m) is False


def test_human_reply_is_neither():
    m = _msg(HUMAN_REPLY)
    assert parsing.is_auto_reply(m) is False
    assert parsing.is_bounce(m) is False
    assert parsing.parse_references(m.get("References")) == ["<abc123@outreach.example>"]
    assert parsing.first_address(m.get("From")) == "sam@brightline.example"


def test_opt_out_language_detected():
    m = _msg(OPT_OUT)
    body = parsing.extract_text(m)
    assert parsing.looks_like_unsubscribe(m.get("Subject"), body) is True


def test_quoted_footer_does_not_trigger_unsubscribe():
    # Our own footer appears in the quoted history of almost every reply; only
    # the top of the message should be examined.
    body = "Sounds good, let's talk Thursday.\n\n" + ("-" * 40) + "\n" + \
           "x" * 600 + "\nTo unsubscribe, reply STOP.\n"
    assert parsing.looks_like_unsubscribe("Re: quick question", body) is False

"""The public front page (multi-school mode): what the system is, a demo, contact and sign-in.

Business details come from the environment so they can be changed on the host without code:
BRAND_NAME, BRAND_TAGLINE, CONTACT_WHATSAPP, CONTACT_PHONE, CONTACT_EMAIL, PRICING_NOTE and
DEMO_SCHOOL (the code of the demo school, "demo" by default). Unset contacts are left out.
"""
import re

from flask import current_app, render_template, request

# Same icons as the app (static/js/ui.js).
ICONS = {
    'fees': 'M11.8 10.9c-2.3-.6-3-1.2-3-2.1 0-1.1 1-1.9 2.7-1.9 1.8 0 2.4.8 2.5 2.1h2.2c-.1-1.7-1.1-3.3-3.2-3.8V3h-3v2.2c-1.9.4-3.5 1.7-3.5 3.6 0 2.3 1.9 3.5 4.7 4.1 2.5.6 3 1.5 3 2.4 0 .7-.5 1.8-2.7 1.8-2.1 0-2.9-.9-3-2.1H6c.1 2.2 1.8 3.5 3.7 3.9V21h3v-2.2c1.9-.4 3.5-1.5 3.5-3.6 0-2.8-2.4-3.8-4.7-4.3',
    'payroll': 'M20 6h-4V4c0-1.1-.9-2-2-2h-4c-1.1 0-2 .9-2 2v2H4c-1.1 0-2 .9-2 2v11c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2m-10-2h4v2h-4zm2 13.5c-1.9 0-3.5-1.6-3.5-3.5s1.6-3.5 3.5-3.5 3.5 1.6 3.5 3.5-1.6 3.5-3.5 3.5',
    'rates': 'M6.99 11 3 15l3.99 4v-3H14v-2H6.99zM21 9l-3.99-4v3H10v2h7.01v3z',
    'students': 'M12 3 1 9l11 6 9-4.9V17h2V9zM5 13.2v4L12 21l7-3.8v-4L12 17z',
    'results': 'M5 9.2h3V19H5zM10.6 5h2.8v14h-2.8zm5.6 8H19v6h-2.8z',
    'library': 'M21 5c-1.11-.35-2.33-.5-3.5-.5-1.95 0-4.05.4-5.5 1.5-1.45-1.1-3.55-1.5-5.5-1.5S2.45 4.9 1 6v14.65c0 .25.25.5.5.5.1 0 .15-.05.25-.05C3.1 20.45 5.05 20 6.5 20c1.95 0 4.05.4 5.5 1.5 1.35-.85 3.8-1.5 5.5-1.5 1.65 0 3.35.3 4.75 1.05.1.05.15.05.25.05.25 0 .5-.25.5-.5V6c-.6-.45-1.25-.75-2-1zm0 13.5c-1.1-.35-2.3-.5-3.5-.5-1.7 0-4.15.65-5.5 1.5V8c1.35-.85 3.8-1.5 5.5-1.5 1.2 0 2.4.15 3.5.5v11.5z',
    'statements': 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zm4 18H6V4h7v5h5zM8 12h8v2H8zm0 4h8v2H8zm0-8h3v2H8z',
    'bank': 'M4 10h3v7H4zm6.5 0h3v7h-3zM2 19h20v3H2zm15-9h3v7h-3zm-5-9L2 6v2h20V6z',
    'assets': 'M12 2 2 7v2h20V7zM4 11v7h3v-7zm6.5 0v7h3v-7zM17 11v7h3v-7zM2 20v2h20v-2z',
    'users': 'M12 1 3 5v6c0 5.5 3.8 10.7 9 12 5.2-1.3 9-6.5 9-12V5zm0 10.99h7c-.5 4.1-3.3 7.8-7 8.9V12H5V6.3l7-3.1z',
}


def _digits(number):
    return re.sub(r"[^0-9]", "", number or "")


def render_home():
    from .models import School
    cfg = current_app.config
    root = request.environ.get("school.root", request.script_root)
    demo_slug = (cfg.get("DEMO_SCHOOL") or "").strip().lower()
    demo = School.query.filter_by(slug=demo_slug, status="active").first() if demo_slug else None
    wa = _digits(cfg.get("CONTACT_WHATSAPP"))
    brand = cfg.get("BRAND_NAME") or "School Management"
    contact = {
        "whatsapp": f"https://wa.me/{wa}?text=" + "Hello%2C%20I%27d%20like%20to%20know%20more%20about%20" + brand.replace(" ", "%20")
        if wa else None,
        "phone": cfg.get("CONTACT_PHONE") or None,
        "email": cfg.get("CONTACT_EMAIL") or None,
    }
    return render_template("home.html", root=root, brand=brand,
                           tagline=cfg.get("BRAND_TAGLINE") or "School fees, payroll and accounts, built for Zimbabwe",
                           custom_tagline=bool(cfg.get("BRAND_TAGLINE")),
                           demo_url=f"{root}/s/{demo.slug}/" if demo else None, contact=contact,
                           has_contact=any(contact.values()), pricing=cfg.get("PRICING_NOTE") or None, icons=ICONS)

"""Self-contained, escaped HTML for mail clients, with a plain-text alternative."""
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def balance_html(account, balance, *, below_target):
    def safe(value):
        return escape(str(value), quote=True)

    try:
        zone = ZoneInfo(account.timezone or "UTC")
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    checked = balance.as_of.astimezone(zone).strftime("%d %b %Y, %I:%M %p %Z")
    amount = f"{balance.amount:,.2f}"
    target = f"{account.low_balance_threshold:,.2f}"
    gap = abs(balance.amount - account.low_balance_threshold)
    title = "Balance needs your attention" if below_target else "Your balance is on track"
    status = "BELOW YOUR TARGET" if below_target else "TARGET MET"
    accent, tint = ("#b45309", "#fffbeb") if below_target else ("#166534", "#f0fdf4")
    gap_label = "Below target by" if below_target else "Above target by"
    source = {"browser": "Observed on your bank website", "provider": "Verified bank connection",
              "statement": "Validated bank statement"}.get(balance.source, "Verified balance record")
    note = ("This is the statement closing balance for the date shown, not a live balance."
            if balance.balance_type == "statement_closing" else
            "This report reflects the balance at the time shown. Your balance may have changed since then.")
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{safe(title)}</title></head>
<body style="margin:0;padding:0;background:#f3f5f8;color:#172033;font-family:'Segoe UI',Arial,Helvetica,sans-serif;-webkit-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{safe(status)} · {safe(balance.currency)} {amount} · {safe(account.nickname)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f5f8"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px">
<tr><td style="padding:0 8px 20px;font-size:14px;font-weight:700;letter-spacing:3px;color:#42526b">MERIDIAN <span style="font-weight:400;letter-spacing:0;color:#68758a">/ Account monitoring</span></td></tr>
<tr><td style="background:#ffffff;border:1px solid #e2e7ee;border-radius:20px;overflow:hidden">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">
<tr><td style="height:6px;background:{accent};font-size:1px;line-height:6px">&nbsp;</td></tr>
<tr><td style="padding:32px 28px 20px"><span style="display:inline-block;padding:7px 12px;border-radius:20px;background:{tint};color:{accent};font-size:11px;font-weight:700;letter-spacing:1px">{status}</span>
<h1 style="margin:18px 0 10px;font-size:27px;line-height:1.25;font-weight:650;letter-spacing:-0.6px">{safe(title)}</h1>
<p style="margin:0;font-size:15px;line-height:1.6;color:#667085">{safe(account.nickname)} &nbsp;·&nbsp; {safe(account.account_number_masked)}</p></td></tr>
<tr><td style="padding:0 28px 24px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f8fafc;border:1px solid #e8edf3;border-radius:14px"><tr><td style="padding:24px">
<p style="margin:0 0 8px;font-size:12px;color:#667085;letter-spacing:1px;text-transform:uppercase">{safe(balance.balance_type.replace('_', ' '))} balance</p>
<p style="margin:0;font-size:36px;font-weight:700;line-height:1.2;letter-spacing:-1px;font-variant-numeric:tabular-nums">{safe(balance.currency)} {amount}</p>
<p style="margin:14px 0 0;font-size:14px;color:{accent}">{gap_label} <strong>{safe(balance.currency)} {gap:,.2f}</strong></p>
</td></tr></table></td></tr>
<tr><td style="padding:0 28px 28px"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="font-size:14px;line-height:1.6">
<tr><td style="padding:10px 0;color:#667085;border-bottom:1px solid #edf0f5">Your minimum target</td><td align="right" style="padding:10px 0;font-weight:600;border-bottom:1px solid #edf0f5">{safe(balance.currency)} {target}</td></tr>
<tr><td style="padding:10px 0;color:#667085;border-bottom:1px solid #edf0f5">Checked at</td><td align="right" style="padding:10px 0;border-bottom:1px solid #edf0f5">{safe(checked)}</td></tr>
<tr><td style="padding:10px 0;color:#667085">Source</td><td align="right" style="padding:10px 0">{safe(source)}</td></tr>
</table><p style="margin:22px 0 0;padding:16px;background:{tint};border-radius:10px;font-size:13px;line-height:1.7;color:{accent}">{'Review this account and restore the balance to your preferred target.' if below_target else 'Your recorded balance meets the minimum target you set.'}</p>
<p style="margin:20px 0 0;font-size:12px;line-height:1.7;color:#7a8596">{safe(note)} Open this account in Meridian to view the saved evidence and manage alerts.</p></td></tr>
</table></td></tr>
<tr><td align="center" style="padding:22px 12px;font-size:11px;line-height:1.8;color:#8490a2">Automatically generated by your Meridian banking software.<br>Bank passwords, OTPs and statement files are never included in this email.</td></tr>
</table></td></tr></table></body></html>'''

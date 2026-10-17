"""Persian local result gallery; no remote dependencies."""
from html import escape
from app.upload_page import STAGES
from app.export.theme import browser_theme

STYLE='''<style>*{box-sizing:border-box}body{background:var(--color-base-50);color:var(--color-base-800);font:15px Tahoma,Arial,sans-serif;margin:0;line-height:1.8}main{max-width:1120px;margin:36px auto;padding:24px}h1{font-size:28px;margin:0 0 10px}p{color:var(--color-base-600)}a{color:var(--color-primary-900)}.table{overflow:auto}table{width:100%;border-collapse:collapse;background:var(--color-base-25);margin-top:28px}td,th{text-align:right;padding:18px;border-bottom:1px solid var(--color-base-200)}th{color:var(--color-base-600);font-size:13px}.button,button{display:inline-block;color:var(--color-base-25);background:var(--color-primary-800);border:1px solid var(--color-primary-800);padding:10px 16px;border-radius:7px;text-decoration:none;cursor:pointer;font:inherit;white-space:nowrap}input{display:block;background:var(--color-base-25);color:var(--color-base-800);border:1px solid var(--color-base-300);border-radius:6px;padding:12px;width:100%;margin:8px 0 18px;direction:ltr}form{max-width:520px}.small{font-size:12px;color:var(--color-base-600)}.error{color:var(--color-error-800)}code{direction:ltr;unicode-bidi:isolate;font-size:12px}</style>'''
HEAD='<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>نتایج سی‌تی</title>'


def digits(value):return str(value).translate(str.maketrans('0123456789','۰۱۲۳۴۵۶۷۸۹'))


def login_page(tenant,error=''):
    return HEAD+'<style>'+browser_theme()+'</style>'+STYLE+'<main><h1>نتایج سی‌تی</h1><p>توکن سرویس و شناسهٔ مرکز را مانند Swagger وارد کنید. پس از ورود، نشست مشاهدهٔ نتایج برای یک ساعت برقرار می‌شود.</p><p class="error">'+escape(error)+'</p><form method="post" action="/results/login"><label>توکن سرویس<input name="token" type="password" required autocomplete="off"></label><label>شناسهٔ مرکز<input name="tenant" value="'+escape(tenant,quote=True)+'" required></label><button>مشاهدهٔ نتایج</button></form><p><a href="/docs">آپلود فایل و مستندات API</a></p></main></html>'


def results_page(rows):
    body=[]
    for row in rows:
        selected=next((s for s in row.get('series',[]) if s['index']==row.get('selected_series_index')),None)
        name='سی‌تی '+row['job_id'][:8]
        if selected:name+=' · '+digits(selected.get('slices','؟'))+' برش'
        link=row.get('viewer_url')
        label='بازکردن نمایشگر ↗' if row['status']=='succeeded' else 'مشاهدهٔ پیشرفت ↗'
        action='<a class="button" target="_blank" rel="noopener noreferrer" href="'+escape(link,quote=True)+'">'+label+'</a>' if link else '<span class="small">نمایشگر در دسترس نیست</span>'
        if row.get('archive_url'):action+='<br><a target="_blank" rel="noopener noreferrer" href="'+escape(row['archive_url'],quote=True)+'">دریافت ZIP مستقل</a>'
        quality={'standard':'استاندارد','fast':'سریع'}.get(row['quality'],row['quality'])
        status=STAGES.get(row.get('stage'),STAGES.get(row['status'],row['status']))
        error='<div class="small"><code>'+escape(row['safe_error'])+'</code></div>' if row.get('safe_error') else ''
        body.append('<tr><td>'+escape(name)+'<div class="small"><code>'+escape(row['job_id'])+'</code></div></td><td>'+escape(status)+error+'</td><td>'+digits(len(row['structures']))+' ناحیه · '+escape(quality)+'</td><td>'+action+'</td></tr>')
    return HEAD+'<style>'+browser_theme()+'</style><meta http-equiv="refresh" content="15">'+STYLE+'<main><h1>نتایج سی‌تی</h1><p>نمایشگر هر سی‌تی در برگه‌ای جدا باز می‌شود. با چک‌باکس‌ها می‌توانید چند ناحیه را هم‌زمان بررسی کنید.</p><p><a href="/docs">آپلود ZIP جدید در Swagger</a> · <a href="/results">به‌روزرسانی نتایج</a></p><div class="table"><table><thead><tr><th>سی‌تی</th><th>وضعیت</th><th>ناحیه‌بندی</th><th>مشاهده</th></tr></thead><tbody>'+(''.join(body) or '<tr><td colspan="4">هنوز فایلی آپلود نشده است.</td></tr>')+'</tbody></table></div><p class="small">به‌روزرسانی هر ۱۵ ثانیه · تصاویر و نتایج روی همین سرور نگه‌داری می‌شوند.</p></main></html>'

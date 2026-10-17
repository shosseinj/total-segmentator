# اجرای segmentation روی سیستم GPU — Ubuntu/Linux

## اجرای مستقیم با محیط Python موجود (بدون Docker)

روی این دستگاه مسیر محیط آماده `/home/sharif/Music/whisper/.venv` است؛ بزرگی و کوچکی حروف مسیر مهم است. لانچر جدید همین Python را به‌کار می‌گیرد و PyTorch موجود را جایگزین نمی‌کند. PostgreSQL، Redis و MinIO همچنان روی دستگاه سایت می‌مانند.

```bash
bash run-native.sh init
bash run-native.sh check-local
```

`check-local` وزن‌های مدل، اجرای واقعی CUDA و import مدل و DICOM را بررسی می‌کند؛ به سرویس‌های راه‌دور نیاز ندارد. برای Python دیگر می‌توانید `SEG_PYTHON=/path/to/.venv/bin/python bash run-native.sh check-local` اجرا کنید.

روی دستگاه سایت، داخل پوشه SharifClinic، دستورهای زیر را اجرا کنید تا نام حساب، IP و مسیر پروژه مشخص شوند:

```bash
whoami
hostname -I
pwd
bash services/dicom-segmentation/deploy/gpu/clinic-control.sh "$PWD" export
```

پیش‌فرض‌های فعلی در ابتدای `run-native.sh` ذخیره شده‌اند: حساب SSH برابر `newuser@192.168.100.20` و مسیر پروژه برابر `/home/newuser/Videos/SharifClinic`. برای دستگاه دیگر همین دو مقدار را در فایل runner تغییر دهید؛ می‌توانید آن‌ها را با متغیرهای `CLINIC_SSH_TARGET` و `CLINIC_ROOT` در `.env` هم جایگزین کنید.

اگر SSH روی دستگاه سایت آماده نیست، همان‌جا `sudo apt install openssh-server` و `sudo systemctl enable --now ssh` را اجرا کنید. در صورت فعال‌بودن UFW، `sudo ufw allow OpenSSH` لازم است. برای ورود بدون درخواست مکرر رمز، روی GPU یک بار اجرا کنید و رمز حساب دستگاه سایت را فقط در ترمینال وارد کنید:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub newuser@192.168.100.20
```

کلید این دستگاه در `~/.ssh/id_ed25519` آماده شده است. برای GPU دیگر، اگر کلید ندارید، ابتدا `ssh-keygen -t ed25519` اجرا کنید. کلید خصوصی را به پروژه یا دستگاه دیگر کپی نکنید.

سپس روی GPU فایل خصوصی را بگیرید و تونل دائمی کاربر را شروع کنید:

```bash
bash run-native.sh fetch-connection
bash run-native.sh tunnel-start
```

روی GPU بررسی کنید:

```bash
bash run-native.sh check
```

پس از موفقیت بررسی، روی دستگاه سایت اجرا کنید (جزئیات انتقال در مرحله ۶ پایین آمده است):

```bash
cd /home/newuser/Videos/SharifClinic
bash services/dicom-segmentation/deploy/gpu/clinic-control.sh "$PWD" activate
```

سپس روی GPU:

```bash
bash run-native.sh fetch-handoff
bash run-native.sh start
bash run-native.sh status
```

اجرای دوباره `start` هنگامی که سرویس‌ها روشن هستند، سلامت API و worker را بررسی می‌کند و `ALREADY_RUNNING` برمی‌گرداند؛ سرویس‌ها بازراه‌اندازی و migration تکرار نمی‌شوند.

این مسیر هم رسید انتقال معتبر می‌خواهد؛ تنظیمات اتصال را `source` نکنید. مسیرهای `/work` و `/models` فایل export در اجرای native به `work/` و `models/` همین پروژه نگاشت می‌شوند و تنظیمات خصوصی تغییر نمی‌کنند. سرویس‌های API، inventory، dispatcher، یک GPU worker و تونل SSH با `systemd --user` اجرا می‌شوند و با بستن ترمینال ادامه می‌دهند. تونل پس از قطع اتصال دوباره تلاش می‌کند؛ `start` هم آن را به‌صورت خودکار شروع می‌کند. شروع خودکار پس از reboot تنظیم نشده است؛ پس از ورود دوباره، `bash run-native.sh start` را اجرا کنید. دستور `connect` همچنان برای تونل foreground با ترمینال باز در دسترس است.

API در اجرای native فعلی با `SEG_API_HOST=0.0.0.0` در `.env` روی همهٔ رابط‌ها گوش می‌دهد. روی دستگاه GPU از `http://127.0.0.1:8096` و روی دستگاه‌های دیگرِ همین شبکه از `http://192.168.100.96:8096` استفاده کنید؛ Swagger در `/docs` است. IP شبکه با تغییر شبکه یا DHCP ممکن است عوض شود؛ `hostname -I` آن را نشان می‌دهد. برای بازگشت به دسترسی فقط محلی، `SEG_API_HOST=127.0.0.1` بگذارید و سرویس API را restart کنید. تونل قبلی دستگاه سایت در `http://127.0.0.1:8098` همچنان برقرار است. `/health/live` و `/health/ready` وضعیت را نشان می‌دهند؛ درخواست‌های API به توکن سرویس نیاز دارند؛ مسیرهای اتصال کلینیک به هدرهای مرکز و کاربر هم نیاز دارند، ولی مسیرهای آپلود محلی آن‌ها را خودکار تعیین می‌کنند. endpointهای اصلی `/v1/capabilities`، `/v1/scans`، `/v1/batches` و `/v1/jobs/{id}/result` هستند.

Swagger روی GPU در `http://127.0.0.1:8096/docs` و روی دستگاه سایت در `http://127.0.0.1:8098/docs` باز می‌شود؛ آدرس `/` هم به آن هدایت می‌شود. در تنظیم فعلی `SEG_SWAGGER_AUTO_AUTHORIZE=true` در فایل خصوصی `connection.env` فعال است؛ Swagger مقدار `SEG_SERVICE_TOKEN` همان فایل را خودکار وارد می‌کند و زدن **Authorize** لازم نیست. این حالت آزمایشی توکن را در صفحهٔ Swagger به مرورگر می‌دهد؛ توکن داخل OpenAPI، فایل HTML نتیجه، URL یا localStorage قرار نمی‌گیرد. برای خاموش‌کردن پرشدن خودکار هنگام اتصال پروژهٔ اصلی، مقدار `SEG_SWAGGER_AUTO_AUTHORIZE=false` بگذارید و فقط سرویس API را راه‌اندازی مجدد کنید (`systemctl --user restart sharif-segmentation-gpu-api.service`). در حالت عادی، توکن را بدون پیشوند Bearer در **Authorize** وارد کنید. کلاینت پروژهٔ اصلی همچنان `Authorization: Bearer <SEG_SERVICE_TOKEN>` می‌فرستد؛ احراز هویت خود API غیرفعال نشده است. برای آپلود محلی، انتخاب فایل کافی است؛ چهار فیلد `X-Tenant-Id`، `X-Actor-Id`، `X-Operator` و `Idempotency-Key` از فرم‌های محلی حذف شده‌اند. مرکز از تنظیمات سرور و شناسهٔ اجرا به‌صورت `local-operator` تعیین می‌شود؛ توکن سرویس مجوز اجرای آپلود محلی را می‌دهد. هدرهای اختیاری قبلی برای کلاینت‌های موجود همچنان پشتیبانی می‌شوند و مرکز نادرست، کاربر نامعتبر یا `X-Operator: false` اجازهٔ ایجاد کار نمی‌دهند. برای مسیرهای اتصال کلینیک مانند scans و batches، `X-Tenant-Id` را برابر `SEG_TENANT_ID` و `X-Actor-Id` را برابر شناسه کاربر مثل `operator` بگذارید. برای درخواست‌های ایجاد، لغو یا تکرار کار، `X-Operator` باید `true` باشد و `Idempotency-Key` هم لازم است. مشخصات OpenAPI در `/openapi.json` هستند؛ توکن فقط در حالت آزمایشی فعال به صفحهٔ Swagger اضافه می‌شود و در schema قرار نمی‌گیرد.

برای اجرای مستقیم فقط API و worker محلی، بدون اتصال به PostgreSQL، Redis یا MinIO، اجرا کنید:

```bash
bash run-native.sh start-local
```

برای دریافت مستقیم فایل نهایی، در `http://127.0.0.1:8096/docs` بخش **آپلود ZIP و نتایج** و **POST /v1/upload/** را باز کنید. در حالت فعلی بدون ورود دستی توکن، با **Try it out** فایل ZIP را انتخاب کنید و یک بار **Execute** بزنید. Swagger مرحلهٔ جاری و زمان انتظار را نمایش می‌دهد و `viewer.html` نهایی را خودکار دریافت می‌کند. پاسخ واقعی API، `200 text/html` با `Content-Disposition: attachment; filename="viewer.html"` است؛ JSON کوچک در پنل Swagger فقط رسید دانلود مرورگر است.

**نتیجه روی سرور نگه‌داری نمی‌شود.** برای اجرای TotalSegmentator، ZIP ورودی، DICOM استخراج‌شده، CT و ماسک در پوشهٔ موقت `work/local-uploads/<job_id>/` قرار می‌گیرند. پیش از ارسال پاسخ موفق، HTML به حافظه خوانده می‌شود و کل پوشهٔ درخواست حذف می‌شود. در خطای فایل یا مدل هم پوشه پاک می‌شود. در پایان مهلت یا قطع اتصال، کار صف‌شده فوراً حذف می‌شود؛ اگر مدل در حال اجرا باشد، لغو علامت‌گذاری می‌شود و worker پس از توقف/خروج مدل همهٔ فایل‌ها را حذف می‌کند. کنترل لغو مدل حداکثر هر ۳۰ ثانیه است؛ این پاک‌سازی از حذف فایل‌های زیر دست مدل جلوگیری می‌کند. فایل‌های موقت رهاشده پس از راه‌اندازی دوبارهٔ API یا انقضای درخواست توسط worker جمع‌آوری می‌شوند. برای جلوگیری از محدودیت طول مسیر سوکت‌های multiprocessing، مدل از یک مسیر کوتاه خصوصی در `/tmp` استفاده می‌کند که فقط پیوندی به پوشهٔ موقت همان درخواست است؛ داده‌ها داخل پوشهٔ درخواست می‌مانند و پیوند کوتاه هم بعد از خروج مدل حذف می‌شود.

فقط HTML تحویل داده می‌شود. ZIP نتیجه، خروجی DICOM SEG، فایل JSON اندازه‌گیری، PNG پیش‌نمایش یا CSS جداگانه برای آپلود ساخته نمی‌شود. فایل HTML شامل برش‌های CT، ماسک‌ها، نام فارسی/لاتین اعضا، حجم‌ها و CSS استخراج‌شده از `theme.css` است و بدون اینترنت یا پروژهٔ اصلی باز می‌شود. دکمه‌های دانلود فایل‌های جانبی از این نمایشگر حذف شده‌اند. HTML دریافتی را در دستگاه خود یا بک‌اند ذخیره کنید؛ در صورت نیاز می‌توانید همان فایل را در MinIO خودتان قرار دهید. سرویس GPU هیچ داده‌ای از این مسیر به MinIO، PostgreSQL یا Redis نمی‌فرستد.

پیش‌فرض `structures=all` همهٔ ۱۱۷ ناحیهٔ مدل را پردازش می‌کند؛ فقط ناحیه‌های موجود در محدودهٔ CT ماسک غیرخالی دارند. برای انتخاب محدود، نام‌های لاتین را با ویرگول مانند `liver,spleen` وارد کنید؛ فهرست در `GET /v1/uploads/capabilities` است. `quality` یکی از `standard` یا `fast` است. گزینهٔ استفادهٔ مجدد از نتیجه حذف شده است؛ هر درخواست تازه مدل را اجرا می‌کند چون ماسک قبلی ذخیره نمی‌شود. فایل‌های نصب‌شدهٔ مدل حفظ می‌شوند و هر بار دانلود نمی‌شوند.

پیش‌فرض `series_index=-1` یا مقدار خالی، CT معتبر با بیشترین برش را انتخاب می‌کند؛ در تعداد برش برابر شمارهٔ کوچک‌تر انتخاب می‌شود. مقدار `0` واقعاً سری صفر است. سری نامعتبر با `422 ITEM_NOT_ELIGIBLE` و راهنمای کوتاه فارسی رد می‌شود؛ برای انتخاب خودکار، `-1` را وارد کنید و دوباره ارسال کنید. `wait_timeout` به‌صورت پیش‌فرض ۳۶۰۰ ثانیه است. پایان مهلت با JSON و کد `504` پاسخ داده می‌شود؛ HTML ناقص تحویل داده نمی‌شود.

Swagger کلید درخواست را خودکار می‌سازد و با **GET /v1/upload/progress** مرحلهٔ جاری را بررسی می‌کند. کلاینت‌ها می‌توانند `Idempotency-Key` اختیاری بفرستند؛ کلید هم‌زمانِ در حال اجرا با `409 UPLOAD_IN_PROGRESS` رد می‌شود. بعد از تحویل یا حذف درخواست، سابقه‌ای برای بازیابی نتیجه با کلید قبلی وجود ندارد. **GET /v1/uploads/{id}** فقط هنگام فعال‌بودن پردازش قابل استفاده است و بعد از پاک‌سازی `404` می‌دهد. فهرست نتیجه، لینک نمایشگر ذخیره‌شده و endpoint دریافت فایل‌های جانبی دیگر ارائه نمی‌شوند؛ `/results` به Swagger هدایت می‌شود. مسیر قدیمی **POST /v1/uploads** نیز همان HTML نهایی را با پاسخ `200` برمی‌گرداند و دیگر صف JSON با پاسخ `202` نیست.

نمایشگر فارسی و راست‌به‌چپ است و انتخاب هم‌زمان اعضا با چک‌باکس، جست‌وجو، یافتن برش عضو، انتخاب همه/هیچ، پنجرهٔ بافت نرم/ریه/استخوان/مغز، تنظیم HU، معکوس‌کردن روشنایی، بزرگ‌نمایی و جابه‌جایی، تمام‌صفحه، پخش برش‌ها، خط‌کش میلی‌متری، ذخیرهٔ عکس از برش، مرز ماسک و شدت رنگ دارد. نما محوری است. CSS از `theme.css` پروژه در HTML جاسازی می‌شود و رنگ‌بندی‌های پیش‌فرض، `isarmed` و `lungimed` قابل انتخاب‌اند. همهٔ اعضا در یک برش نیستند؛ از انتخاب همه و یافتن عضو استفاده کنید. اندازه‌گیری حجم مربوط به محدودهٔ همین CT است و نتایج خودکار نیاز به بازبینی پزشک دارند.

worker محلی و worker کلاینت از قفل مشترک GPU استفاده می‌کنند. سقف ZIP پیش‌فرض ۱۰ GiB و حجم استخراج‌شده ۳۰ GiB است. مسیرهای کلاینت `/v1/scans` و `/v1/batches` گردش‌کار اصلی خود را دارند؛ برای دریافت صرفاً HTML از `/v1/upload/` استفاده کنید.

```bash
bash run-native.sh logs
bash run-native.sh stop
```

`stop` برای پایان کار در حال اجرا فرصت می‌دهد و سپس تونل مدیریت‌شده را متوقف می‌کند؛ فایل‌های خروجی و سرویس‌های دستگاه دیگر را حذف نمی‌کند. برای کنترل جداگانه تونل، `tunnel-start` و `tunnel-stop` در دسترس هستند. مراحل بعدی این راهنما مربوط به مسیر Docker هستند؛ برای اجرای native نیازی به build Docker نیست.

این بسته شامل کد سرویس، Docker، اسکریپت‌های اجرا و وزن‌های آمادهٔ CT است. سایت، ورود و Viewer روی سیستم فعلی باقی می‌مانند. PostgreSQL، Redis و MinIO فعلی از طریق تونل SSH استفاده می‌شوند. API، inventory، recovery و GPU worker همگی روی سیستم GPU و با فضای کاری مشترک اجرا خواهند شد.

ساخت و اجرای این بسته به‌خودی‌خود هیچ scan یا batch ایجاد نمی‌کند. بعد از آماده‌شدن، فقط دو ZIP منتخب را از سایت برای اولین آزمون تأیید کنید.

## ۱. پیش‌نیازهای سیستم GPU

این دستورها را روی **سیستم GPU** بررسی کنید:

```bash
nvidia-smi
docker info
docker compose version
python3 --version
ssh -V
df -h .
```

- Ubuntu/Linux روی x86_64 با Docker Engine محلی و Compose نسخهٔ 2.30 یا جدیدتر؛ حساب فعلی باید اجازهٔ اجرای Docker داشته باشد.
- درایور NVIDIA و NVIDIA Container Toolkit باید برای Docker تنظیم شده باشند. اسکریپت ما درایور یا Docker daemon را تغییر/ری‌استارت نمی‌کند و پروژه‌های دیگر را متوقف نمی‌کند.
- اتصال اینترنت برای ساخت اولیهٔ image و دریافت بسته‌های Python لازم است. وزن‌های CT داخل ZIP هستند و دوباره دانلود نمی‌شوند.
- فضای دیسک برای ZIP، وزن‌های استخراج‌شده، image CUDA و scratch لازم است؛ حدود ۳۰ GiB فضای آزاد برای شروع با نمونه‌های منتخب در نظر بگیرید و اندازهٔ ZIPهای بعدی را جدا بررسی کنید. مصرف واقعی GPU/RAM باید در آزمون ثبت شود؛ ظرفیت مشخصی هنوز benchmark نشده است.
- کارت انتخابی باید حافظهٔ آزاد داشته باشد. اگر پروژهٔ دیگری از همان GPU استفاده می‌کند، کارت آزاد انتخاب کنید یا اجرای آن‌ها را نوبتی کنید.

راهنماهای رسمی نصب، در صورت آماده‌نبودن میزبان:

- Docker روی Ubuntu: https://docs.docker.com/engine/install/ubuntu/
- NVIDIA Container Toolkit و تنظیم Docker: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
- انتخاب GPU در Compose: https://docs.docker.com/compose/how-tos/gpu-support/

## ۲. استخراج بسته روی سیستم GPU

ZIP و فایل `.sha256` را کنار هم بگذارید:

```bash
sha256sum -c sharif-segmentation-gpu.zip.sha256
unzip sharif-segmentation-gpu.zip
cd sharif-segmentation-gpu
bash run.sh init
```

فایل `.env` فقط تنظیمات غیرمحرمانه دارد. `SEG_GPU_DEVICE=0` یعنی کارت شمارهٔ صفر از خروجی `nvidia-smi`. برای کارت دیگر، فقط این عدد را تغییر دهید. در مسیر مستندشده پورت‌ها را روی 8096 و 8098 نگه دارید.

فایل `connection.env.example` فقط نمونه است؛ برای نصب فعلی، فایل واقعی را از مرحلهٔ بعد بگیرید. آن را `source` نکنید و مقدارهایش را در چت نفرستید.

## ۳. خروجی تنظیمات خصوصی — روی سیستم فعلی سایت

این مرحله روی **سیستم فعلی پروژهٔ SharifClinic** اجرا می‌شود، نه روی GPU. کد همین پروژه باید شامل ابزارهای جدید باشد. این تغییرها در workspace فعلی آماده شده‌اند؛ اگر سایت را از checkout دیگری اجرا می‌کنید، ابتدا همین نسخهٔ کد را به آن منتقل کنید. داخل ریشهٔ پروژه اجرا کنید:

```bash
cd /home/newuser/Videos/SharifClinic
bash services/dicom-segmentation/deploy/gpu/clinic-control.sh "$PWD" export
```

این دستور فقط تنظیمات موجود را می‌خواند و فایل خصوصی زیر را با دسترسی 600 می‌سازد؛ سرویس‌ها را متوقف نمی‌کند:

```text
local-backend/segmentation/gpu-connection.env
```

این فایل شامل رمزهای اتصال است و عمداً داخل ZIP عمومی قرار نگرفته است. حساب SSH باید همان حساب دارای دسترسی به پروژه باشد.

اگر سرویس SSH روی سیستم سایت آماده نیست، مسئول همان سیستم باید آن را فعال کند. برای Ubuntu، دستورهای پایه عبارت‌اند از:

```bash
sudo apt-get update
sudo apt-get install openssh-server
sudo systemctl enable --now ssh
whoami
hostname -I
```

نام حساب از `whoami` و IP قابل دسترسی از سیستم GPU را استفاده کنید. در شبکه‌های جدا، IP شبکهٔ VPN را به‌کار ببرید؛ `127.0.0.1` آدرس سیستم دیگر نیست. [راهنمای رسمی Ubuntu](https://documentation.ubuntu.com/server/how-to/security/openssh-server/)

## ۴. گرفتن تنظیمات و برقراری تونل — روی سیستم GPU

در پوشهٔ استخراج‌شده، `CLINIC_USER` و `CLINIC_IP` را با حساب و IP سیستم فعلی سایت جایگزین کنید:

```bash
scp CLINIC_USER@CLINIC_IP:/home/newuser/Videos/SharifClinic/local-backend/segmentation/gpu-connection.env ./connection.env
chmod 600 connection.env
bash connect.sh CLINIC_USER@CLINIC_IP
```

آخرین دستور در foreground می‌ماند؛ **این ترمینال را باز نگه دارید**. در اولین اتصال، fingerprint میزبان SSH را بررسی کنید. اگر سیستم سایت از بیرون قابل دسترسی نیست، ابتدا دو دستگاه را با LAN یا VPN به هم متصل کنید. سرویس SSH سیستم سایت باید اتصال حساب شما و local/remote forwarding را مجاز کند.

تونل‌ها همگی روی loopback هستند:

| روی سیستم GPU | مقصد در سیستم سایت |
| --- | --- |
| 127.0.0.1:15432 | PostgreSQL موجود |
| 127.0.0.1:16379 | Redis موجود |
| 127.0.0.1:19000 | MinIO موجود |
| API سیستم GPU روی 8096 | از سیستم سایت با 127.0.0.1:8098 در دسترس می‌شود |

PostgreSQL، Redis و MinIO جدید نسازید و پورت‌هایشان را روی اینترنت عمومی باز نکنید. آدرس‌های داخلی Docker سیستم سایت مثل `postgres` و `minio` روی دستگاه دیگر قابل استفاده نیستند؛ فایل export آن‌ها را به آدرس تونل تبدیل می‌کند.

## ۵. ساخت و بررسی — ترمینال دوم سیستم GPU

```bash
cd /path/to/sharif-segmentation-gpu
bash run.sh build
bash run.sh check
```

`build` در اولین اجرا ممکن است طول بکشد. نسخه‌ها: Python 3.12، PyTorch 2.8.0 با CUDA 12.6 و TotalSegmentator 2.11.0. [منبع نسخهٔ PyTorch](https://pytorch.org/get-started/previous-versions/#v280)

`check` دیتابیس، Redis، checksum وزن‌ها، فضای قابل نوشتن، خواندن باکت ورودی و put/get/delete یک فایل تشخیصی کوچک و بی‌هویت در باکت خروجی را بررسی می‌کند. هیچ ZIP بیمار دانلود یا مدل segmentation اجرا نمی‌شود. تست CUDA یک محاسبهٔ کوچک واقعی روی GPU انجام می‌دهد.

خروجی مورد انتظار شامل این موارد است:

```text
POSTGRES_OK
REDIS_OK
INPUT_BUCKET_READ_ACCESS_OK
OUTPUT_PUT_GET_VERIFY_DELETE_OK
CHECKPOINTS_VERIFIED_FILES=75
SCRATCH_WRITABLE_OK
CUDA_KERNEL_OK
GPU_NAME=...
```

در صورت `NATIVE_WORKERS_RUNNING` روی سیستم سایت، لانچر native را با همان Python سرویس و `scripts/run-local.py --stop` متوقف کنید و پس از اتمام فرایندها دوباره activate را اجرا کنید. `--stop` درخواست توقف است؛ helper تا خروج واقعی فرایندها انتقال را رد می‌کند.

در صورت شکست check، هنوز به مرحلهٔ انتقال نروید؛ کد خطا را بررسی کنید. `GPU_UNAVAILABLE` معمولاً یعنی Docker به GPU دسترسی ندارد. ناسازگاری درایور/کارت ممکن است در تست kernel مشخص شود؛ هنوز هیچ تأیید واقعی GPU برای این دستگاه ثبت نشده است.

## ۶. انتقال کنترل — روی سیستم فعلی سایت

بعد از پاس‌شدن check، این دستور را روی **سیستم فعلی سایت** اجرا کنید:

```bash
cd /home/newuser/Videos/SharifClinic
bash services/dicom-segmentation/deploy/gpu/clinic-control.sh "$PWD" activate
```

این کار:

1. اجرای native زندهٔ ثبت‌شده را از PID، پوشهٔ کاری و command marker شناسایی و انتقال را رد می‌کند. سپس وجود job فعال را بررسی می‌کند؛ اگر فعال باشد متوقف می‌شود تا ابتدا کار تمام یا لغو شود.
2. فقط API/inventory/dispatcher/GPU worker متعلق به Compose فعلی segmentation را متوقف و توقفشان را بررسی می‌کند؛ PostgreSQL، Redis، MinIO و پروژه‌های دیگر متوقف نمی‌شوند.
3. یک نسخهٔ پشتیبان تنظیمات API و رسید امضاشدهٔ `gpu-handoff.json` می‌سازد.
4. اتصال Viewer را به `http://127.0.0.1:8098` تغییر می‌دهد و Viewer را با اسکریپت‌های فعلی بازراه‌اندازی می‌کند. فقط فرایندهای خود Viewer بازراه‌اندازی می‌شوند؛ برای چند لحظه Viewer در دسترس نخواهد بود و نشست باز آن نیاز به بازشدن مجدد دارد؛ ورود اصلی پروژه بازنویسی نمی‌شود.

رسید یک پیش‌شرط عملیاتی برای انتقال است، نه قفل توزیع‌شده؛ تا وقتی deployment GPU فعال است Compose محلی segmentation را دوباره روشن نکنید. در این فاصله تا مرحلهٔ بعد، پنل segmentation ممکن است موقتاً آماده نباشد.

## ۷. شروع سرویس — روی سیستم GPU

```bash
scp CLINIC_USER@CLINIC_IP:/home/newuser/Videos/SharifClinic/local-backend/segmentation/gpu-handoff.json ./handoff.json
chmod 600 handoff.json
bash run.sh start
bash run.sh status
```

`start` بدون رسید معتبرِ همان تنظیمات اجرا نمی‌شود. migration روی دیتابیس موجود اجرا می‌شود و سپس سرویس‌ها بالا می‌آیند. پورت 8096 باید آزاد باشد. منتظر این خروجی بمانید:

```text
HANDOFF_VALID
API_READY_GPU_WORKER_READY
```

API فقط روی loopback گوش می‌کند؛ از سیستم سایت به آن تونل می‌زنیم. worker فقط یک job را هم‌زمان روی یک GPU اجرا می‌کند. وزن‌ها read-only هستند و فایل‌های موقت در volume مشترک `scratch` نگه‌داری می‌شوند. این volume را هنگام اجرای worker حذف یا بین دو میزبان جدا نکنید.

## ۸. اولین آزمون واقعی

در سایت فعلی، یک سند DICOM منتخب را باز کنید:

1. «بررسی فایل‌ها»؛ این مرحله inference نمی‌کند.
2. یک سری CT معتبر، عضو موردنظر و کیفیت استاندارد را انتخاب کنید.
3. «شروع پردازش موارد انتخاب‌شده» را صریح بزنید.
4. وضعیت running/exporting/uploading/succeeded را دنبال کنید.
5. نتیجه را باز کنید، overlay را در Viewer وارد کنید و همان سری مرجع را انتخاب کنید؛ اندازه‌ها فقط برای سری مرجع نمایش داده می‌شوند.
6. برای شروع مجموعاً فقط دو ZIP منتخب را آزمایش کنید؛ کل باکت را تأیید نکنید.

آزمون دوبارهٔ تنظیمات یکسان باید نتیجهٔ معتبر قبلی را reuse کند. فقط بعد از این آزمون، انتشار واقعی و overlay را پذیرفته‌شده ثبت کنید. کیفیت پزشکی خروجی هنوز نیاز به بازبینی فرد متخصص دارد؛ نتایج خودکار unreviewed هستند. MR در این بسته غیرفعال است.

## توقف، گزارش و برگشت

روی **سیستم GPU**:

```bash
bash run.sh status
bash run.sh logs
bash run.sh stop
```

`stop` داده‌های خروجی، دیتابیس و volume را حذف نمی‌کند. پس از توقف، می‌توانید تونل SSH را با Ctrl+C ببندید. لاگ‌ها را پیش از اشتراک بررسی کنید؛ فایل `connection.env` و رسیدهای خصوصی را نفرستید.

برای بازگشت کامل به اجرای CPU قبلی، ابتدا deployment GPU را با `stop` خاموش کنید، حدود ۱۰۰ ثانیه برای منقضی‌شدن heartbeat صبر کنید و سپس روی **سیستم سایت** اجرا کنید:

```bash
cd /home/newuser/Videos/SharifClinic
bash services/dicom-segmentation/deploy/gpu/clinic-control.sh "$PWD" rollback
```

rollback در صورت job فعال، heartbeat تازه یا API راه‌دور روشن، اجرا نمی‌شود. اگر GPU در میانهٔ job قطع شده، قبل از rollback پایان worker را احراز و همان deployment GPU را برای recovery راه‌اندازی کنید؛ مالکیت job را دستی از دیتابیس پاک نکنید.

## وضعیت آزمون بسته

`VERIFICATION.md` و `bundle-manifest.json` مشخص می‌کنند چه چیزی در محیط ساخت بررسی شده است. آماده‌سازی ZIP به‌معنای آزمون روی GPU شما نیست. اجرای `check` و سپس دو آزمون واقعی انتخاب‌شده، بخش باقی‌ماندهٔ پذیرش است.

اگر چیزی شکست خورد، نام مرحله، کد خطا و خروجی `nvidia-smi`/`run.sh status` را بفرستید؛ مقدار credentialها و تصاویر بیمار لازم نیست.

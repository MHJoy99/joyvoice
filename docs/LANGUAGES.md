# JoyVoice — Multilingual Reach: Hindi, Arabic, Spanish + English Reference

> Quick-start (install → `F8` → paste) for three high-reach languages plus English reference.
> Any of the 10 supported languages can be **source AND target** — set `language` (source) and `target_language` (target) independently.
> Full setup: `docs/SETUP.md`. Questions: `docs/FAQ.md`. Fixes: `docs/TROUBLESHOOTING.md`.

**Global rule (all sections):** `language` = what you speak (`auto` or one of 10 codes). `target_language` = what gets pasted (any of the 10 codes). `output_mode`: `translation` / `original` / `both`.

---

## English Reference — Install, Use, Settings

### Install
1. Windows 10/11 + Python 3.11 + mic.
2. `git clone https://github.com/MHJoy99/joyvoice.git && cd joyvoice`
3. `python -m venv .venv` then isolated install: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install -r requirements.txt`
4. Set key: `$env:JV_API_KEY="YOUR_KEY"` or Settings → API tab.
5. Launch: `.venv\Scripts\python app\main.py` (or `run.bat`).

### Use (F8 → paste)
1. Focus any app (Word, Chrome, VS Code, Slack).
2. Press `F8` → speak → press `F8` again to stop.
3. Wait for blue `transcribing` → green `pasted`. Text auto-pastes via clipboard-safe `Ctrl+V`.
4. Hold mode alternative: hold `F8` while speaking (Settings → Hotkey → `hotkey_mode: hold`).

### Settings (target language)
- Settings → Output → **Source language** (`language`: `auto`, `bn`, `en`, `ru`, `hi`, `es`, `ar`, `zh`, `ja`, `fr`, `pt`) + **Target language** (`target_language`: any of the same 10).
- Example: speak Hindi (`hi`) → paste English (`en`): `language: hi`, `target_language: en`, `output_mode: translation`.
- Any of the 10 can be source AND target. `output_mode: both` pastes `original + \n\n + translation`.

### 10-language support table
| Code | Language | Native | Code | Language | Native |
| :--- | :------- | :----- | :--- | :------- | :----- |
| `bn` | Bangla | বাংলা | `en` | English | English |
| `ru` | Russian | Русский | `hi` | Hindi | हिन्दी |
| `es` | Spanish | Español | `ar` | Arabic | العربية |
| `zh` | Chinese | 中文 | `ja` | Japanese | 日本語 |
| `fr` | French | Français | `pt` | Portuguese | Português |

> Note: any of the 10 above can be source AND target.

### FAQ link
- `docs/FAQ.md` (Q5 languages, Q6 translation, Q8 run), `docs/SETUP.md` (full install), `docs/TROUBLESHOOTING.md` (mic/hotkey/paste fixes).

---

## हिन्दी मार्गदर्शिका — इंस्टॉल, उपयोग, सेटिंग

### इंस्टॉल करें
1. विंडोज़ 10/11 + पाइथन 3.11 + माइक्रोफ़ोन चाहिए।
2. `git clone https://github.com/MHJoy99/joyvoice.git` फिर `cd joyvoice`।
3. `python -m venv .venv` फिर: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install -r requirements.txt`
4. कुंजी सेट करें: `$env:JV_API_KEY="YOUR_KEY"` या सेटिंग्स → API टैब।
5. चलाएँ: `.venv\Scripts\python app\main.py` (या `run.bat`)।

### उपयोग करें (F8 → पेस्ट)
1. कोई भी ऐप खोलें (Word, Chrome, VS Code)।
2. `F8` दबाएँ → बोलें (हिन्दी) → फिर `F8` दबाकर रोकें।
3. नीला `transcribing` → हरा `pasted` देखें। पाठ अपने आप पेस्ट होगा (`Ctrl+V`)।
4. होल्ड मोड: `F8` दबाकर रखें, बोलें, छोड़ें (सेटिंग्स → Hotkey → `hotkey_mode: hold`)।

### सेटिंग (लक्ष्य भाषा)
- सेटिंग्स → Output → **स्रोत भाषा** (`language`: `auto` या 10 में से कोई) + **लक्ष्य भाषा** (`target_language`: उन्हीं 10 में से कोई)।
- उदाहरण: हिन्दी बोलें → अंग्रेज़ी पाएँ: `language: hi`, `target_language: en`, `output_mode: translation`।
- 10 में से कोई भी भाषा स्रोत AND लक्ष्य बन सकती है। `output_mode: both` से दोनों मिलते हैं।

### 10-भाषा समर्थन तालिका
| कोड | भाषा | मूल लिपि | कोड | भाषा | मूल लिपि |
| :--- | :---- | :-------- | :--- | :---- | :-------- |
| `bn` | बांग्ला | বাংলা | `en` | अंग्रेज़ी | English |
| `ru` | रूसी | Русский | `hi` | हिन्दी | हिन्दी |
| `es` | स्पेनिश | Español | `ar` | अरबी | العربية |
| `zh` | चीनी | 中文 | `ja` | जापानी | 日本語 |
| `fr` | फ्रेंच | Français | `pt` | पुर्तगाली | Português |

> नोट: ऊपर की 10 में से कोई भी भाषा स्रोत AND लक्ष्य हो सकती है।

### FAQ लिंक
- `docs/FAQ.md` (भाषाएँ, अनुवाद, चलाने का तरीका), `docs/SETUP.md` (पूरा इंस्टॉल), `docs/TROUBLESHOOTING.md` (माइक/हॉटकी सुधार)।

---

## دليل العربية — التثبيت والاستخدام والإعدادات

> ملاحظة RTL مع الحفاظ على Markdown آمن LTR: هذا الملف يبقى LTR بالكامل. النص العربي داخل الفقرات فقط، ولا يُستخدم داخل الجداول إلا في عمود Native. لا تغيّر اتجاه الجداول أو الكتل البرمجية. الأكواد (`hi`, `ar`, `F8`) تبقى LTR داخل backticks. الأرقام والمسارات تبقى LTR.

### التثبيت
1. تحتاج Windows 10/11 مع Python 3.11 مع ميكروفون.
2. انسخ المستودع: `git clone https://github.com/MHJoy99/joyvoice.git` ثم `cd joyvoice`.
3. أنشئ البيئة: `python -m venv .venv` ثم ثبّت بمعزل: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install -r requirements.txt`
4. اضبط المفتاح: `$env:JV_API_KEY="YOUR_KEY"` أو من Settings ثم API.
5. التشغيل: `.venv\Scripts\python app\main.py` أو `run.bat`.

### الاستخدام (F8 ثم لصق)
1. ركّز على أي تطبيق (Word أو Chrome أو VS Code).
2. اضغط `F8` ثم تحدث بالعربية ثم اضغط `F8` مرة أخرى للإيقاف.
3. انتظر الأزرق `transcribing` ثم الأخضر `pasted`. سيُلصق النص تلقائيا عبر `Ctrl+V`.
4. وضع الضغط المستمر: استمر بالضغط على `F8` أثناء الكلام ثم أفلته (Settings ثم Hotkey ثم `hotkey_mode: hold`).

### الإعدادات (لغة الهدف)
- من Settings ثم Output: **لغة المصدر** (`language`: قيمة `auto` أو واحد من 10 رموز) مع **لغة الهدف** (`target_language`: أي واحد من نفس الرموز 10).
- مثال: تحدث بالعربية والصق بالإنجليزية: `language: ar` مع `target_language: en` مع `output_mode: translation`.
- أي واحدة من اللغات 10 يمكن أن تكون مصدر AND هدف. الوضع `output_mode: both` يلصق الاثنين معا.

### جدول دعم اللغات 10
| Code | Language | Native | Code | Language | Native |
| :--- | :------- | :----- | :--- | :------- | :----- |
| `bn` | Bangla | বাংলা | `en` | English | English |
| `ru` | Russian | Русский | `hi` | Hindi | हिन्दी |
| `es` | Spanish | Español | `ar` | Arabic | العربية |
| `zh` | Chinese | 中文 | `ja` | Japanese | 日本語 |
| `fr` | French | Français | `pt` | Portuguese | Português |

> ملاحظة: أي واحدة من اللغات 10 أعلاه يمكن أن تكون مصدر AND هدف.

### رابط الأسئلة
- `docs/FAQ.md` (اللغات والترجمة والتشغيل) و `docs/SETUP.md` (التثبيت الكامل) و `docs/TROUBLESHOOTING.md` (إصلاح الميكروفون).

---

## Guía en español — Instalación, uso, ajustes

### Instalación
1. Windows 10/11 + Python 3.11 + micrófono.
2. `git clone https://github.com/MHJoy99/joyvoice.git && cd joyvoice`
3. `python -m venv .venv` y luego: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install -r requirements.txt`
4. Clave: `$env:JV_API_KEY="YOUR_KEY"` o Ajustes → pestaña API.
5. Iniciar: `.venv\Scripts\python app\main.py` (o `run.bat`).

### Uso (F8 → pegar)
1. Enfoca cualquier app (Word, Chrome, VS Code, Slack).
2. Pulsa `F8` → habla en español → pulsa `F8` otra vez para detener.
3. Azul `transcribing` → verde `pasted`. El texto se pega solo con `Ctrl+V` seguro.
4. Modo mantener: mantén `F8` mientras hablas y suelta (Ajustes → Hotkey → `hotkey_mode: hold`).

### Ajustes (idioma de destino)
- Ajustes → Output → **Idioma de origen** (`language`: `auto` o uno de 10 códigos) + **Idioma de destino** (`target_language`: cualquiera de los mismos 10).
- Ejemplo: hablas español → pegas inglés: `language: es`, `target_language: en`, `output_mode: translation`.
- Cualquiera de los 10 puede ser origen AND destino. `output_mode: both` pega ambos.

### Tabla de 10 idiomas
| Código | Idioma | Nativo | Código | Idioma | Nativo |
| :----- | :----- | :----- | :----- | :----- | :----- |
| `bn` | Bengalí | বাংলা | `en` | Inglés | English |
| `ru` | Ruso | Русский | `hi` | Hindi | हिन्दी |
| `es` | Español | Español | `ar` | Árabe | العربية |
| `zh` | Chino | 中文 | `ja` | Japonés | 日本語 |
| `fr` | Francés | Français | `pt` | Portugués | Português |

> Nota: cualquiera de los 10 anteriores puede ser origen AND destino.

### Enlace FAQ
- `docs/FAQ.md` (idiomas, traducción, ejecución), `docs/SETUP.md` (instalación completa), `docs/TROUBLESHOOTING.md` (micrófono/hotkey/pegado).

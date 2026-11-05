# Bot de Telegram: noticias de oro, Bitcoin y petróleo

Vigila feeds RSS de fuentes de referencia (Reserva Federal, EIA, CNBC, MarketWatch, BBC, CoinDesk, Cointelegraph, OilPrice, Yahoo Finance), filtra lo que afecta a 🥇 oro, ₿ Bitcoin y 🛢 petróleo, evita duplicados y te lo manda a Telegram.

- 🔴 **Alto impacto**: decisiones de la Fed, inflación/empleo, OPEP, sanciones, guerra, ETF/SEC, hackeos, máximos/caídas fuertes.
- 🟡 **Relevante**: cualquier otra noticia que menciona directamente el activo.
- 🌐 **Macro**: noticias de la Fed, inflación, etc. que no nombran un activo, solo si son de alto impacto.

## 1. Crear el bot en Telegram (2 minutos)

1. En Telegram, abre **@BotFather** y escribe `/newbot`. Elige nombre y usuario.
2. Copia el **token** que te da.
3. Abre tu bot nuevo, pulsa **Iniciar** y escríbele "hola".

## 2. Instalar (PowerShell, dentro de la carpeta del bot)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Abre `.env` y pega `TELEGRAM_BOT_TOKEN`. Luego obtén tu chat id:

```powershell
python bot.py --chat-id
```

Copia la línea `TELEGRAM_CHAT_ID=...` que sale al `.env`.

## 3. Probar

```powershell
python bot.py --test-feeds      # qué fuentes responden
python bot.py --test-telegram   # te llega un mensaje de prueba
```

Si alguna fuente da ERROR, el bot la salta y sigue con las demás. Puedes borrarla o cambiarla en la lista `FEEDS` de `bot.py`.

## 4. Ejecutar

```powershell
python bot.py
```

La primera vez marca las noticias actuales como vistas (para no mandarte un aluvión) y te avisa de que está activo. Revisa cada 120 s.

## Ajustes (en `.env`)

| Variable | Qué hace |
|---|---|
| `POLL_SECONDS` | Cada cuántos segundos revisa (por defecto 120) |
| `ONLY_HIGH_IMPACT=1` | Solo avisos 🔴, menos ruido |
| `MAX_AGE_HOURS` | Ignora noticias más viejas (por defecto 12 h) |
| `USE_OLLAMA=1` | Añade resumen en español y sesgo alcista/bajista con tu `qwen3:14b` local (más lento, y el modelo puede equivocarse) |

## Dejarlo siempre encendido

Solo funciona mientras el PC esté encendido y el script corriendo. Para que arranque solo en Windows: Programador de tareas → Crear tarea → desencadenador "Al iniciar sesión" → acción: `...\.venv\Scripts\pythonw.exe` con argumento `bot.py` y "Iniciar en" la carpeta del bot. Para 24/7 sin depender del PC, haría falta un servidor (por ejemplo un VPS barato).

## Límites que conviene saber

- Los RSS tienen retraso de minutos: es "casi tiempo real", no un feed de trading profesional.
- Los titulares llegan en inglés (salvo que actives Ollama).
- El filtro es por palabras clave: puede dejar pasar alguna noticia irrelevante o perder alguna relevante. Ajusta `ASSETS`, `MACRO_KW` y `HIGH_KW` en `bot.py`.
- Son avisos informativos, no señales de compra o venta, ni asesoramiento financiero.
- Guarda el token en secreto. Si se filtra, genera uno nuevo con `/revoke` en BotFather.

# Waschtermin

Web-App, über die Waschstraßen freie Waschtermine anbieten und Autofahrer diese buchen.
Waschstraßen zahlen ein monatliches Abo; die komplette Terminverwaltung läuft über die App.

## Starten

```bash
pip install flask
python3 app.py            # http://localhost:5000
python3 test_flow.py      # kompletter Testlauf
```

Konfiguration über Umgebungsvariablen:

| Variable | Standard | Bedeutung |
|---|---|---|
| `SECRET_KEY` | `dev-nur-lokal-aendern` | **Im Betrieb zwingend setzen** (langer Zufallswert) |
| `DATABASE` | `waschtermin.db` | Pfad der SQLite-Datei |
| `MONTHLY_FEE_CENTS` | `5900` | Monatsbeitrag in Cent (59 €). Abgerechnet wird in Euro; in den Sprachen kk/ru wird ein gerundeter Näherungswert in ₸ (Tenge) angezeigt, in de/en Euro (Kurse in `FEE_CURRENCIES` in `app.py`) |
| `PAYMENT_MODE` | `demo` | `demo` = Zahlung wird nur simuliert |
| `PORT` | `5000` | Port |

## Funktionen

**Waschstraße:** Registrierung mit Adresse, Termine einzeln oder als Zeitraum anlegen (Dauer, Preis,
Leistung, parallele Waschplätze), Buchungen einsehen und stornieren, Profil bearbeiten, Abo verwalten.
Ohne aktives Abo sind alle Termine für Autofahrer unsichtbar und nicht buchbar.

**Autofahrer:** Registrierung, Suche nach Ort/PLZ/Name, Datum und Leistung, Buchung mit optionalem
Kennzeichen, eigene Buchungen ansehen und stornieren.

**Technik:** Doppelbuchungen sind per Datenbank-Index ausgeschlossen. Passwörter werden gehasht,
alle Formulare sind CSRF-geschützt, Rollen und Besitzrechte werden bei jeder Aktion geprüft.

## Noch nötig für den Echtbetrieb

1. **Zahlungsanbieter anbinden** (z. B. Stripe Billing oder Mollie): In `wash_pay()` einen Checkout
   starten und `extend_subscription()` erst nach bestätigter Zahlung per Webhook aufrufen. Für ein
   echtes Abo mit automatischer Verlängerung und Kündigung ist das der größte offene Punkt.
2. **Hosting mit HTTPS** (z. B. hinter nginx/Caddy mit `gunicorn app:app`), `SECRET_KEY` setzen,
   `SESSION_COOKIE_SECURE=True`.
3. **E-Mail-Versand** (Buchungsbestätigung, Passwort zurücksetzen, E-Mail-Verifizierung) –
   aktuell gibt es keinen.
4. **Rechtliches:** Impressum, Datenschutzerklärung, AGB inkl. Abo-Bedingungen/Widerruf.
5. Optional: Umkreissuche (Geokoordinaten), Kalenderansicht, Bewertungen, Stornofristen.

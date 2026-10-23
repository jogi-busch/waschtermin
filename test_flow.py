"""Spielt den kompletten Ablauf durch: python3 test_flow.py"""
import os
import re
import tempfile
from datetime import datetime, timedelta

tmp = tempfile.mkdtemp()
os.environ["DATABASE"] = os.path.join(tmp, "test.db")

import app as waschtermin  # noqa: E402

waschtermin.app.config["TESTING"] = True


def client():
    return waschtermin.app.test_client()


def token(c):
    c.get("/anmelden")
    with c.session_transaction() as s:
        return s["csrf"]


def post(c, url, data=None, **kw):
    data = dict(data or {})
    data["csrf"] = token(c)
    return c.post(url, data=data, follow_redirects=True, **kw)


def check(label, cond):
    print(("OK   " if cond else "FAIL ") + label)
    assert cond, label


tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

wash = client()
r = post(wash, "/registrieren/wash", dict(
    name="Blitzblank", email="wash@example.com", password="geheim123",
    street="Hauptstr. 1", zip="65183", city="Wiesbaden", description="Schnell & sauber"))
check("Waschstraße registriert, landet auf Abo-Seite", "Abonnement" in r.text)

# Slots anlegen ohne Abo
r = post(wash, "/waschstrasse/termine", dict(
    date=tomorrow, time_from="08:00", time_to="10:00", duration="30", bays="1",
    price="12,50", service="Basiswäsche"))
check("4 Termine angelegt", "4 Termin(e) angelegt" in r.text)

driver = client()
r = post(driver, "/registrieren/driver", dict(name="Max", email="max@example.com", password="geheim123"))
r = driver.get("/suche?q=Wiesbaden")
check("Ohne Abo keine Termine sichtbar", "Keine freien Termine" in r.text)

# Abo aktivieren
r = post(wash, "/waschstrasse/abo/zahlen")
check("Abo aktiviert", "Abo für 30 Tage verlängert" in r.text)
r = driver.get("/suche?q=Wiesbaden")
slot_ids = re.findall(r"/termine/(\d+)/buchen", r.text)
check("Mit Abo 4 Termine sichtbar", len(slot_ids) == 4)

# Filter
check("Filter Datum ohne Treffer", "Keine freien Termine" in driver.get("/suche?date=2000-01-01").text)
check("Filter Ort ohne Treffer", "Keine freien Termine" in driver.get("/suche?q=Hamburg").text)

# Buchen + Doppelbuchung
r = post(driver, f"/termine/{slot_ids[0]}/buchen", dict(car_note="WI-AB 123"))
check("Gebucht", "Termin gebucht" in r.text and "WI-AB 123" in r.text)

driver2 = client()
post(driver2, "/registrieren/driver", dict(name="Eva", email="eva@example.com", password="geheim123"))
r = post(driver2, f"/termine/{slot_ids[0]}/buchen")
check("Doppelbuchung verhindert", "bereits vergeben" in r.text or "nicht mehr verfügbar" in r.text)
check("Gebuchter Termin nicht mehr in Suche", slot_ids[0] not in re.findall(r"/termine/(\d+)/buchen", driver2.get("/suche").text))

# Waschstraße sieht Buchung, kann gebuchten Slot nicht löschen
r = wash.get("/waschstrasse")
check("Waschstraße sieht Buchung", "max@example.com" in r.text)
r = post(wash, f"/waschstrasse/termine/{slot_ids[0]}/loeschen")
check("Gebuchter Slot nicht löschbar", "gebucht" in r.text.lower() and "Termin gelöscht" not in r.text)

# Fahrer storniert -> Slot wieder frei
booking_id = re.search(r"/meine-termine/(\d+)/stornieren", driver.get("/meine-termine").text).group(1)
r = post(driver, f"/meine-termine/{booking_id}/stornieren")
check("Fahrer-Storno", "Termin storniert" in r.text)
check("Slot wieder buchbar", slot_ids[0] in re.findall(r"/termine/(\d+)/buchen", driver2.get("/suche").text))

# Eva bucht, Waschstraße storniert
post(driver2, f"/termine/{slot_ids[0]}/buchen")
bid = re.search(r"/waschstrasse/buchungen/(\d+)/stornieren", wash.get("/waschstrasse").text).group(1)
r = post(wash, f"/waschstrasse/buchungen/{bid}/stornieren")
check("Waschstraßen-Storno", "wieder frei" in r.text)
check("Eva sieht Storno", "Von der Waschstraße storniert" in driver2.get("/meine-termine").text)

# Rechte
check("Fahrer darf nicht ins Waschstraßen-Dashboard", driver.get("/waschstrasse").status_code == 403)
check("Waschstraße darf nicht suchen/buchen", wash.get("/suche").status_code == 403)
anon = client()
check("Anonym -> Login-Redirect", anon.get("/suche").status_code == 302)
check("POST ohne CSRF abgelehnt", anon.post("/anmelden", data={"email": "a", "password": "b"}).status_code == 400)

# Fremde Slots/Buchungen
other = client()
post(other, "/registrieren/wash", dict(name="Andere", email="o@example.com", password="geheim123",
                                         street="x", zip="1", city="y"))
check("Fremden Slot nicht löschbar", post(other, f"/waschstrasse/termine/{slot_ids[1]}/loeschen").status_code == 404)
check("Fremde Buchung nicht stornierbar", post(other, f"/waschstrasse/buchungen/{bid}/stornieren").status_code == 404)
check("Fahrer kann fremde Buchung nicht stornieren", post(driver, f"/meine-termine/{bid}/stornieren").status_code == 404)

# Abo läuft ab -> Termine unsichtbar und nicht buchbar
db = waschtermin.sqlite3.connect(os.environ["DATABASE"])
db.execute("UPDATE washes SET subscription_until = '2000-01-01T00:00' WHERE name = 'Blitzblank'")
db.commit()
check("Abgelaufenes Abo: Termine unsichtbar", "Keine freien Termine" in driver2.get("/suche").text)
r = post(driver2, f"/termine/{slot_ids[2]}/buchen")
check("Abgelaufenes Abo: Buchung abgelehnt", "nicht mehr verfügbar" in r.text)

print("\nAlle Tests bestanden.")

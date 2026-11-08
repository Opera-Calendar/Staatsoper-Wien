#!/usr/bin/env python3
"""Rolling opera-only Staatsoper Unter den Linden calendar.
Discover operas on official season works indexes; read each production's scheduled performances.
An unsuccessful scrape never overwrites an existing calendar.
"""
import datetime as dt
import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event, Alarm

BASE = 'https://www.staatsoper-berlin.de'
OUT = Path('staatsoper-berlin-opera.ics')
TZ = ZoneInfo('Europe/Berlin')
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; personal opera calendar)'}
MONTHS = {'januar':1,'februar':2,'märz':3,'maerz':3,'april':4,'mai':5,'juni':6,
          'juli':7,'august':8,'september':9,'oktober':10,'november':11,'dezember':12}
# Full dates are printed beside the location in the performance sections of each work page.
FULL_DATE = re.compile(
    r'(?P<day>\d{1,2})\.\s*(?P<month>Januar|Februar|März|Maerz|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)'
    r'\s+(?P<year>20\d{2})\s+(?P<hour>\d{1,2})[.:](?P<minute>\d{2})\s*Uhr'
    r'\s+Staatsoper\s+Unter\s+den\s+Linden\b', re.I)
DURATION = re.compile(r'Dauer:\s*(?:ca\.?\s*)?(?:(\d{1,2}):([0-5]\d)\s*h|(\d{1,2})\s*h(?:\s*(\d{1,2})\s*min)?)',re.I)

session = requests.Session()
session.headers.update(HEADERS)

def fetch(url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text,'html.parser')

def index_urls(today):
    # Season changes in late summer, so include previous/current and next seasons.
    return [f'{BASE}/de/spielplan/werke/{year}-{year+1}/'
            for year in range(today.year-1,today.year+3)]

def production_urls(today):
    found = set()
    successful = 0
    for index in index_urls(today):
        try:
            page = fetch(index)
        except requests.RequestException as exc:
            logging.warning('Werkübersicht nicht erreichbar %s: %s', index, exc)
            continue
        # A missing season can sometimes redirect to an unrelated page: require work links.
        links = {urljoin(BASE, a['href']).split('#')[0].split('?')[0]
                 for a in page.select('a[href]')
                 if re.search(r'/de/veranstaltungen/[^/]+\.\d+/?$', a['href'])}
        if not links:
            logging.warning('Keine Werk-Links in %s', index)
            continue
        successful += 1
        found.update(links)
    if not successful or len(found)<5:
        raise RuntimeError(f'Keine ausreichende offizielle Werkübersicht gefunden ({len(found)} Werke); Kalender bleibt unverändert')
    logging.info('Werkübersichten: %s erreichbar; %s Werkseiten', successful, len(found))
    return sorted(found)

def duration_from(text):
    match = DURATION.search(text)
    if not match:
        return dt.timedelta(hours=3), False
    if match.group(1):
        mins = int(match.group(1))*60+int(match.group(2))
    else:
        mins = int(match.group(3))*60+int(match.group(4) or 0)
    return dt.timedelta(minutes=mins), True

def collect_events(today):
    result = {}
    failed = 0
    for url in production_urls(today):
        try:
            page = fetch(url)
        except requests.RequestException as exc:
            failed += 1
            logging.warning('Werkseite nicht erreichbar %s: %s',url,exc)
            continue
        h1 = page.find('h1')
        if not h1:
            continue
        title = h1.get_text(' ',strip=True)
        # The works index is explicitly opera-only; no broad keyword guessing necessary.
        body = page.get_text(' ', strip=True)
        duration, accurate = duration_from(body)
        for match in FULL_DATE.finditer(body):
            start = dt.datetime(int(match.group('year')),MONTHS[match.group('month').lower()],
                                int(match.group('day')),int(match.group('hour')),
                                int(match.group('minute')),tzinfo=TZ)
            if not (today <= start.date() <= today+dt.timedelta(days=570)):
                continue
            # Stable UID across updates, independent of event ordering.
            key = (url,start.isoformat())
            result[key] = dict(url=url,title=title,start=start,end=start+duration,
                               duration_verified=accurate)
    logging.info('Werkseiten-Fehler: %s; Aufführungen: %s',failed,len(result))
    items = sorted(result.values(),key=lambda i:i['start'])
    if len(items)<5:
        raise RuntimeError(f'Nur {len(items)} Aufführungen gefunden; Kalender bleibt unverändert')
    return items

def write_calendar(items):
    cal = Calendar()
    for prop, value in [('prodid','-//Opera Calendar//Staatsoper Unter den Linden//DE'),
                        ('version','2.0'),('calscale','GREGORIAN'),('method','PUBLISH'),
                        ('x-wr-calname','Staatsoper Unter den Linden – Opern'),
                        ('x-wr-timezone','Europe/Berlin')]:
        cal.add(prop,value)
    timestamp = dt.datetime.now(dt.timezone.utc)
    for item in items:
        ev = Event()
        ev.add('uid',hashlib.sha256(f"{item['url']}|{item['start'].isoformat()}".encode()).hexdigest()[:32]+'@opera-calendar')
        ev.add('dtstamp',timestamp)
        ev.add('dtstart',item['start'])
        ev.add('dtend',item['end'])
        ev.add('summary',item['title'])
        ev.add('location','Staatsoper Unter den Linden, Unter den Linden 7, 10117 Berlin')
        note = 'Offiziell angegebene Dauer' if item['duration_verified'] else 'Dauer nicht angegeben: 3 Stunden Platzhalter'
        ev.add('description',f"Opernvorstellung. {note}. Quelle: {item['url']}")
        ev.add('url',item['url'])
        for days in (90,30,7,1):
            alarm=Alarm()
            alarm.add('action','DISPLAY')
            alarm.add('description',f"{item['title']} – Staatsoper Berlin")
            alarm.add('trigger',dt.timedelta(days=-days))
            ev.add_component(alarm)
        cal.add_component(ev)
    OUT.write_bytes(cal.to_ical())

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    items=collect_events(dt.datetime.now(TZ).date())
    write_calendar(items)
    print(f'Kalender aktualisiert: {len(items)} Vorstellungen -> {OUT}')

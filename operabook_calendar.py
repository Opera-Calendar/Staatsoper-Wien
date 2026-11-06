#!/usr/bin/env python3
"""Wiener Staatsoper opera-only subscriptions (performances / ticket sale starts).

Scrapes the publicly visible official opera calendar. No invented start times.
Ticket-sale alarms live inside ticket-sale events (no separate reminder events).
The website may change its HTML; this scraper reports extraction counts and fails
rather than silently publishing an empty calendar.
"""
from __future__ import annotations

import calendar
import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta

BASE = 'https://www.wiener-staatsoper.at'
TZ = ZoneInfo('Europe/Vienna')
HEADERS = {'User-Agent': 'OperaCalendarSubscription/1.0 (public schedule; personal use)'}
MONTH_NAMES = ('januar','februar','maerz','april','mai','juni','juli','august','september','oktober','november','dezember')
MONTH_ABBR = {'jan':1,'feb':2,'mär':3,'mrz':3,'mar':3,'apr':4,'mai':5,'jun':6,'jul':7,'aug':8,'sep':9,'okt':10,'nov':11,'dez':12}
DAY_RE = re.compile(r'^(Mo|Di|Mi|Do|Fr|Sa|So)\.$')
DATE_RE = re.compile(r'^(\d{1,2})\.?\s+([A-Za-zÄÖÜäöü]{3,10})\.?$')
CLOCK_RE = re.compile(r'^(\d{1,2}:\d{2})(?:\s*[—–-]\s*(\d{1,2}:\d{2}))?$')
SALE_RE = re.compile(r'(?:Ticketverkauf\s+ab|Vorverkauf(?:sbeginn)?\s*(?:am|:))\s*(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s*(?:um|,)\s*(\d{1,2}:\d{2}))?', re.I)
EXCLUDED = ('opernball','generalprobe','ballett','ballet','konzert','matinée','matinee','führung','kinderoper')
ROLE_LABELS = {'musikalische leitung', 'conductor', 'dirigent'}


def esc(value: str) -> str:
    return str(value).replace('\\','\\\\').replace(';','\\;').replace(',','\\,').replace('\n','\\n')


def fold(line: str) -> str:
    out = []
    while len(line.encode('utf-8')) > 74:
        cut = 70
        while len(line[:cut].encode('utf-8')) > 72:
            cut -= 1
        out.append(line[:cut]); line = ' ' + line[cut:]
    out.append(line)
    return '\r\n'.join(out)


def download(year: int, month: int):
    url = f'{BASE}/kalender/oper/{year}/{MONTH_NAMES[month-1]}/'
    r = requests.get(url, headers=HEADERS, timeout=35)
    r.raise_for_status()
    return url, BeautifulSoup(r.text,'html.parser')


def text_lines(soup):
    for bad in soup(['script','style','noscript','svg']): bad.decompose()
    return [re.sub(r'\s+', ' ',s).strip() for s in soup.get_text('\n', strip=True).splitlines() if s.strip()]


def extract_date_before(lines, marker, year, month):
    """Locate the last nearby real calendar date, even if no time is shown.

    The opera listing sometimes has: weekday / date / composer / work /
    category, without a clock. Do not infer a performance time.
    """
    low = max(0, marker - 13)
    month_token = r'(?:Jan|Feb|Mär|Mrz|Mar|Apr|Mai|Jun|Jul|Aug|Sep|Okt|Nov|Dez)[a-zä]*\.?'
    weekday = {'mo': 0, 'di': 1, 'mi': 2, 'do': 3, 'fr': 4, 'sa': 5, 'so': 6}
    for i in range(marker - 2, low - 1, -1):
        candidates = []
        # Date as one text node, or day/month split into adjacent nodes.
        for width in (1, 2):
            if i + width > marker - 1:
                continue
            sample = ' '.join(lines[i:i + width])
            m = re.fullmatch(rf'(\d{{1,2}})\.?\s+({month_token})', sample, re.I)
            if m:
                candidates.append((int(m.group(1)), m.group(2), i - 1))
        # Weekday may be part of the same node as date.
        m2 = re.fullmatch(rf'(Mo|Di|Mi|Do|Fr|Sa|So)\.?\s+(\d{{1,2}})\.?\s+({month_token})', lines[i], re.I)
        if m2:
            candidates.append((int(m2.group(2)), m2.group(3), i))
        for day, month_name, weekday_pos in candidates:
            mon = MONTH_ABBR.get(month_name.lower().rstrip('.')[:3])
            if mon != month:
                continue
            try:
                d = date(year, mon, day)
            except ValueError:
                continue
            if weekday_pos == i and m2:
                token = m2.group(1).lower()
            elif weekday_pos >= 0 and DAY_RE.fullmatch(lines[weekday_pos]):
                token = lines[weekday_pos][:2].lower()
            else:
                continue
            if d.weekday() == weekday[token]:
                return d
    return None


def parse_month(year, month):
    url, soup = download(year, month)
    lines = text_lines(soup)
    result = []
    markers = rejected_dates = missing_times = 0
    for marker, raw in enumerate(lines):
        current = re.sub(r'\s+', ' ', raw).strip().casefold()
        next_line = lines[marker + 1].strip().casefold() if marker + 1 < len(lines) else ''
        category = (re.fullmatch(r'oper\s+gro(?:ß|ss)er\s+saal', current)
                    or (current == 'oper' and re.fullmatch(r'gro(?:ß|ss)er\s+saal', next_line)))
        if not category:
            continue
        markers += 1
        # A listing ends with composer, then title, then the opera category;
        # preceding clock (if any) is optional in the advance season listings.
        if marker < 2:
            continue
        if lines[marker - 1].casefold() in ('generalprobe', 'vorpremiere'):
            continue
        title, composer = lines[marker - 1], lines[marker - 2]
        if (not 1 < len(title) <= 110 or not 1 < len(composer) <= 110
                or CLOCK_RE.fullmatch(title) or CLOCK_RE.fullmatch(composer)
                or any(x in title.casefold() for x in EXCLUDED)):
            continue
        d = extract_date_before(lines, marker, year, month)
        if d is None:
            rejected_dates += 1
            if rejected_dates <= 2:
                print('DEBUG: date not detected:', repr(lines[max(0, marker-10):marker+2]))
            continue
        # Do not include public/general rehearsals as an opera performance.
        # Rehearsal markers immediately adjoining the title were removed above.
        # Only accept a clock immediately preceding the composer or nearby
        # a premiere tag; never substitute a guessed starting time.
        possible_times = [x for x in lines[max(0,marker-5):marker-2]
                          if CLOCK_RE.fullmatch(x)]
        tm = CLOCK_RE.fullmatch(possible_times[-1]) if possible_times else None
        starttime, endtime = (tm.group(1), tm.group(2)) if tm else (None, None)
        if not tm:
            missing_times += 1
        # Cast info belongs to this performance card, not subscriptions
        # or the next event. The page typically ends cast at 'Laden'.
        tail = lines[marker + (2 if current == 'oper' else 1):marker + 85]
        end_of_card = next((i for i,x in enumerate(tail)
                            if x.casefold() == 'laden' or DAY_RE.fullmatch(x)), len(tail))
        tail = tail[:end_of_card]
        cast_line = next((x for x in tail[:8] if x.casefold().startswith('mit ')), '')
        conductor = ''
        m = re.search(r'Musikalische Leitung:\s*(.+)$', cast_line, re.I)
        if m:
            conductor = m.group(1).strip()
        else:
            for j,x in enumerate(tail[:45]):
                if x.casefold() in ROLE_LABELS and j + 1 < len(tail):
                    conductor = tail[j + 1]
                    break
        cast = re.sub(r',?\s*Musikalische Leitung:.*$', '', cast_line[4:], flags=re.I).strip() if cast_line else ''
        sm = SALE_RE.search(' '.join(tail))
        verified = bool(sm)
        if sm:
            sale = datetime(int(sm[3]), int(sm[2]), int(sm[1]),
                            *(map(int, (sm[4] or '10:00').split(':'))), tzinfo=TZ)
        else:
            sale_month = d.replace(day=1) - relativedelta(months=2)
            sale = datetime(sale_month.year, sale_month.month, 1, 10, 0, tzinfo=TZ)
        result.append(dict(day=d, title=title, composer=composer, start=starttime,
                           end=endtime, conductor=conductor, cast=cast,
                           sale=sale, verified=verified, url=url))
    print(f'DEBUG: {year}-{month:02d}: {markers} opera category markers, '
          f'{rejected_dates} unrecognised dates, {missing_times} times not published, '
          f'{len(result)} operas')
    return result


def event(item, stamp):
    d = item['day']
    key = f'{d.isoformat()}|{item["title"]}|{item["start"] or "unknown"}'
    uid = hashlib.sha256(key.encode()).hexdigest()[:24]+'@vienna-opera-calendar'
    lines = ['BEGIN:VEVENT',f'UID:{uid}',f'DTSTAMP:{stamp}',f'SUMMARY:{esc(item["title"])}',
             'LOCATION:Wiener Staatsoper\\, Wien']
    if item['start']:
        hh,mm = map(int,item['start'].split(':'))
        begin = datetime(d.year,d.month,d.day,hh,mm,tzinfo=TZ)
        if item['end']:
            eh,em = map(int,item['end'].split(':'))
            end = datetime(d.year,d.month,d.day,eh,em,tzinfo=TZ)
            if end <= begin: end += timedelta(days=1)
        else: end = begin + timedelta(hours=3)  # clearly labeled approximate
        lines += [f'DTSTART;TZID=Europe/Vienna:{begin:%Y%m%dT%H%M%S}',
                  f'DTEND;TZID=Europe/Vienna:{end:%Y%m%dT%H%M%S}']
    else:
        lines += [f'DTSTART;VALUE=DATE:{d:%Y%m%d}',f'DTEND;VALUE=DATE:{(d+timedelta(days=1)):%Y%m%d}']
    sale = item['sale']
    description = [f'Composer: {item["composer"]}']
    if item['conductor']: description.append(f'Conductor: {item["conductor"]}')
    if item['cast']: description.append(f'Main cast: {item["cast"]}')
    description.append('End time: official' if item['end'] else ('End time: approx. 3h' if item['start'] else 'Starting time not verified; displayed as all-day'))
    description.append(f'Seated-ticket public sale: {sale:%d.%m.%Y %H:%M} Vienna time (' + ('published on page)' if item['verified'] else 'calculated from official monthly sale policy; verify exceptions') + ')')
    description.append(f'Source: {item["url"]}')
    lines += [f'DESCRIPTION:{esc(chr(10).join(description))}',f'URL:{item["url"]}']
    lines.append('END:VEVENT')
    return lines


def sale_event(sale, items, stamp):
    """One 30-minute calendar event per unique sale-opening datetime, with embedded alerts."""
    uid = hashlib.sha256(sale.isoformat().encode()).hexdigest()[:24]+'@vienna-opera-sales'
    end = sale + timedelta(minutes=30)
    works = sorted(set(x['title'] for x in items))
    details = [f'{x["day"]:%d.%m.%Y}: {x["title"]}' for x in sorted(items,key=lambda x:(x['day'],x['title']))]
    inferred = [x for x in items if not x['verified']]
    description = ['Opera performances going on sale:', *details,
                   'Sale opening: '+sale.strftime('%d.%m.%Y %H:%M')+' Vienna time',
                   'Calculated using the standard monthly sale policy for '+str(len(inferred))+' performances; check for exceptions.' if inferred else 'Sale opening published on the official calendar.',
                   'Reminders are alarms inside this event, not separate calendar events.',
                   'Source: '+BASE+'/kalender/oper/']
    title = 'Wiener Staatsoper – Ticket sales: '+', '.join(works[:3])
    if len(works)>3: title+=f' (+{len(works)-3} more)'
    lines = ['BEGIN:VEVENT',f'UID:{uid}',f'DTSTAMP:{stamp}',
             f'DTSTART;TZID=Europe/Vienna:{sale:%Y%m%dT%H%M%S}',
             f'DTEND;TZID=Europe/Vienna:{end:%Y%m%dT%H%M%S}',
             f'SUMMARY:{esc(title)}','LOCATION:Wiener Staatsoper\\, Wien',
             f'DESCRIPTION:{esc(chr(10).join(description))}',
             f'URL:{BASE}/kalender/oper/']
    for label, offset in [('3 months',relativedelta(months=3)),
                          ('1 month',relativedelta(months=1)),
                          ('1 week',timedelta(days=7)),
                          ('1 day',timedelta(days=1))]:
        alarm = sale-offset
        lines += ['BEGIN:VALARM','ACTION:DISPLAY',
                  f'DESCRIPTION:{esc("Vienna opera ticket sales – "+label+" before")}',
                  f'TRIGGER;VALUE=DATE-TIME:{alarm.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}',
                  'END:VALARM']
    lines.append('END:VEVENT')
    return lines


def write_sales_ics(path, items):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Vienna Opera Calendar//EN',
             'CALSCALE:GREGORIAN','METHOD:PUBLISH',
             'X-WR-CALNAME:Wiener Staatsoper - Ticket Sales',
             'X-WR-TIMEZONE:Europe/Vienna','REFRESH-INTERVAL;VALUE=DURATION:PT24H']
    grouped={}
    for item in items: grouped.setdefault(item['sale'],[]).append(item)
    for sale, performances in sorted(grouped.items()):
        lines.extend(sale_event(sale,performances,stamp))
    lines.append('END:VCALENDAR')
    Path(path).write_bytes(('\r\n'.join(map(fold,lines))+'\r\n').encode('utf-8'))


def write_ics(path, items, name):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Vienna Opera Calendar//EN',
             'CALSCALE:GREGORIAN','METHOD:PUBLISH',f'X-WR-CALNAME:{name}',
             'X-WR-TIMEZONE:Europe/Vienna', 'REFRESH-INTERVAL;VALUE=DURATION:PT24H']
    for item in sorted(items,key=lambda x:(x['day'],x['start'] or '',x['title'])):
        lines.extend(event(item,stamp))
    lines.append('END:VCALENDAR')
    Path(path).write_bytes(('\r\n'.join(map(fold,lines))+'\r\n').encode('utf-8'))


def main():
    now = datetime.now(TZ).date()
    # Search previous month (still relevant recently) and next 18 months; newly released seasons follow automatically.
    first = now.replace(day=1)-relativedelta(months=1)
    all_items=[]
    successes=0
    for i in range(20):
        d=first+relativedelta(months=i)
        try:
            items=parse_month(d.year,d.month)
            items=[x for x in items if x['day'] >= now]
            successes+=1
            print(f'{d:%Y-%m}: {len(items)} operas')
            all_items.extend(items)
        except Exception as exc:
            print(f'WARNING: {d:%Y-%m} unavailable: {exc}')
    if successes < 3 or len(all_items)<5:
        raise RuntimeError('Insufficient confirmed schedule data; existing .ics files left unchanged')
    unique={ (x['day'],x['title'],x['start']):x for x in all_items }
    shows=list(unique.values())
    write_ics('vienna-opera.ics',shows,'Wiener Staatsoper - All Operas')
    write_sales_ics('vienna-ticket-sales.ics',[x for x in shows if x['sale'].date() >= now])
    print(f'Wrote {len(shows)} opera performances and {len(set(x["sale"] for x in shows if x["sale"].date() >= now))} future sale events')

if __name__=='__main__': main()

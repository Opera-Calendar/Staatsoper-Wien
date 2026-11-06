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


def parse_month(year, month):
    url, soup = download(year, month)
    lines = text_lines(soup)
    # The official website groups the entries by "Mon / 08 Feb / [time] / composer / title / Opera Main Stage".
    # Read only the segment before the next weekday, and ignore cast/subscription excerpts.
    result = []
    positions = [i for i, line in enumerate(lines) if DAY_RE.fullmatch(line) and i+1 < len(lines) and DATE_RE.fullmatch(lines[i+1])]
    for idx, start in enumerate(positions):
        stop = positions[idx+1] if idx+1 < len(positions) else len(lines)
        section = lines[start:stop]
        match = DATE_RE.fullmatch(section[1]); day, mon = int(match[1]), MONTH_ABBR.get(match[2].lower()[:3])
        if mon != month: continue
        when = date(year, month, day)
        # The site may separate "Oper" and "Großer Saal" into different
        # HTML text nodes. Detect the category without requiring one string.
        # The event header is: weekday, date, time, composer, work, category.
        def normalized(value):
            return re.sub(r"\s+", " ", value).strip().lower()

        marker = None
        for j in range(2, min(len(section), 24)):
            current = normalized(section[j])
            following = normalized(section[j + 1]) if j + 1 < len(section) else ""
            combined = " ".join(normalized(x) for x in section[j:j+3])
            if (re.fullmatch(r"oper\s+gro(?:ß|ss)er\s+saal", current)
                or (current == "oper" and re.fullmatch(r"gro(?:ß|ss)er saal", following))
                or re.match(r"^oper\s+gro(?:ß|ss)er\s+saal(?:\s|$)", combined)
                or (current in ("großer saal", "grosser saal") and j > 2 and normalized(section[j-1]) == "oper")):
                marker = j
                break
        if marker is None:
            continue
        top = section[2:marker]
        top = [x for x in top if x.lower() not in ('premiere','generalprobe','vorpremiere')]
        times = [CLOCK_RE.fullmatch(x) for x in top if CLOCK_RE.fullmatch(x)]
        starttime = times[0].group(1) if times else None
        endtime = times[0].group(2) if times else None
        names = [x for x in top if not CLOCK_RE.fullmatch(x)]
        if len(names) < 2:
            continue
        composer, title = names[-2], names[-1]
        if len(title) > 110 or len(composer) > 110 or title.lower() in ('details','tickets','vorbestellen'): continue
        if any(b in title.lower() for b in EXCLUDED): continue
        if 'generalprobe' in [s.lower() for s in section[:marker]]: continue
        # Narrow text search so supplementary subscription entries do not become performances.
        tail = section[marker+1:]
        cast_line = next((x for x in tail[:8] if x.lower().startswith('mit ')), '')
        conductor = ''
        if 'Musikalische Leitung:' in cast_line:
            conductor = cast_line.split('Musikalische Leitung:',1)[1].strip()
        else:
            for i, x in enumerate(tail[:45]):
                if x.lower() in ROLE_LABELS and i+1 < len(tail):
                    conductor = tail[i+1]; break
        cast = re.sub(r',?\s*Musikalische Leitung:.*$', '', cast_line[4:]).strip() if cast_line else ''
        # Individual published ticket-sale date, if included in public page text.
        sale = None; verified = False
        sm = SALE_RE.search(' '.join(tail))
        if sm:
            sale = datetime(int(sm[3]),int(sm[2]),int(sm[1]),*(map(int,(sm[4] or '10:00').split(':'))),tzinfo=TZ)
            verified = True
        else:
            # Official regular-seat policy: 1st day of the month, 2 months before show month.
            sale_month = when.replace(day=1) - relativedelta(months=2)
            sale = datetime(sale_month.year,sale_month.month,1,10,0,tzinfo=TZ)
        result.append(dict(day=when,title=title,composer=composer,start=starttime,end=endtime,
                           conductor=conductor,cast=cast,sale=sale,verified=verified,url=url))
    if not result:
        print(f"DEBUG: {year}-{month:02d}: {len(positions)} date sections, "
              f"{sum(1 for x in lines if x.strip().lower() == 'oper')} isolated opera labels, "
              f"{sum(1 for x in lines if 'großer saal' in x.lower())} main-stage labels")
        for pos in positions[:2]:
            print('DEBUG: sample event header:', repr(lines[pos:pos+12]))
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
    print(f'Wrote {len(shows)} opera performances and {len(set(x["sale"] for x in shows))} sale events')

if __name__=='__main__': main()

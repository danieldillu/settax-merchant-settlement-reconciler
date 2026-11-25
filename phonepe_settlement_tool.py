"""Offline PhonePe settlement consolidator. Python 3.9+, standard library only."""
import csv
import io
import os
import re
import sys
import zipfile
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, date, timedelta
import calendar
from pathlib import Path

HEADERS = ["Report Date", "Settlement Date (from CSV)", "Bank Reference", "Transactions", "UPI", "Card",
           "Gross Amount", "Processing Fee", "GST on Processing Fee", "Machine Rental",
           "GST on Machine Rental", "Other Deductions", "Rounding Adjustment", "Total Deductions",
           "Net Bank Entry", "Source CSV"]
AMOUNTS = HEADERS[6:15]
REQUIRED = {"PaymentType", "Instrument", "SettlementDate", "BankReferenceNo", "Amount", "Fee", "IGST", "CGST", "SGST"}
MONEY = Decimal('0.01')

def amt(v):
    try: return Decimal(str(v or '0').strip().replace(',',''))
    except InvalidOperation: raise ValueError(f'Invalid monetary value: {v!r}')

def money(v): return v.quantize(MONEY, rounding=ROUND_HALF_UP)

def fmt(v): return f'{money(v):.2f}'

def dateformat(s):
    s = (s or '').strip()
    for f in ('%d-%m-%Y','%Y-%m-%d','%d/%m/%Y'):
        try: return datetime.strptime(s,f).strftime('%d-%m-%Y')
        except ValueError: pass
    raise ValueError(f'Unrecognized settlement date {s!r}')

def csv_rows(data, name):
    for encoding in ('utf-8-sig','utf-16','cp1252'):
        try:
            value = data.decode(encoding)
            reader = csv.DictReader(io.StringIO(value, newline=''))
            rows = list(reader)
            if reader.fieldnames and ('BankReferenceNo' in reader.fieldnames or 'Bank Reference' in reader.fieldnames):
                return rows, [x.strip() for x in reader.fieldnames]
        except (UnicodeError, csv.Error): pass
    raise ValueError('CSV encoding or headers not recognized')

def parse_settlement(data, name, warnings=None):
    rows, fields = csv_rows(data,name)
    missing = REQUIRED.difference(fields)
    if missing: raise ValueError('Missing columns: ' + ', '.join(sorted(missing)))
    if not rows: raise ValueError('No records')
    refs = {r['BankReferenceNo'].strip() for r in rows if r['BankReferenceNo'].strip()}
    if len(refs)!=1: raise ValueError(f'Expected exactly one bank reference per report, found {len(refs)}')
    ref = refs.pop()
    ds = {dateformat(r['SettlementDate']) for r in rows if r['SettlementDate'].strip()}
    if len(ds)!=1: raise ValueError('Multiple settlement dates in same report')
    settlement_date = ds.pop()
    match = re.search(r'_(\d{8})_(AXNPN[^.]*)\.csv$',Path(name).name,re.I)
    report_date = datetime.strptime(match.group(1),'%Y%m%d').strftime('%d-%m-%Y') if match else settlement_date
    count=upi=card=0
    gross=Decimal(0); processing=Decimal(0); gstprocessing=Decimal(0); rental=Decimal(0); gstrental=Decimal(0); other=Decimal(0)
    raw_net=Decimal(0)
    # Classification is based on the SIGNED settlement components, not just
    # the payment/adjustment label. Negative values reduce the bank settlement.
    # Positive adjustments can reverse prior deductions.
    for index,r in enumerate(rows,2):
        typ = r['PaymentType'].strip().upper()
        instr = r['Instrument'].strip().upper()
        amount,fee,igst,cgst,sgst = (amt(r[k]) for k in ('Amount','Fee','IGST','CGST','SGST'))
        gst=igst+cgst+sgst
        raw_net += amount+fee+gst

        if typ=='PAYMENT' and amount>=0:
            count+=1
            gross+=amount
            if instr.startswith('UPI_'):
                upi+=1
            elif instr.startswith('EDC_') or instr.startswith('CARD_'):
                card+=1
            elif instr=='WALLET_FULFILMENT' or instr.startswith('WALLET_'):
                # Wallet is a payment instrument. Its negative fee/GST columns
                # are costs deducted from the settlement (not bank transfers).
                pass
            elif warnings is not None:
                warnings.append(f'NOTICE {name} row {index}: unrecognized payment instrument {instr}; included in gross and deductions')
            processing+=-fee
            gstprocessing+=-gst
        elif instr=='DEVICE_RECURRING_CHARGE_COLLECTION':
            # Recurring machine charge, regardless of REFUND/ADJUSTMENT label.
            rental+=-(amount+fee)
            gstrental+=-gst
        else:
            # Includes REFUND / VAS_RECURRING_CHARGE_COLLECTION and any other
            # signed adjustment. Never reject a report solely because PhonePe
            # introduced a new adjustment code; preserve the bank reconciliation.
            other+=-(amount+fee+gst)
            if typ=='PAYMENT' and amount<0 and warnings is not None:
                warnings.append(f'NOTICE {name} row {index}: negative PAYMENT amount included as deduction')
            elif (typ,instr) not in {
                ('REFUND','VAS_RECURRING_CHARGE_COLLECTION'),
                ('ADJUSTMENT','VAS_RECURRING_CHARGE_COLLECTION'),
                ('REFUND','WALLET_FULFILMENT'),
                ('ADJUSTMENT','WALLET_FULFILMENT')
            } and warnings is not None:
                warnings.append(f'NOTICE {name} row {index}: {typ} / {instr} included as signed Other Deductions; review classification')
    net=money(raw_net)
    breakdown = [money(processing),money(gstprocessing),money(rental),money(gstrental),money(other)]
    rounding = money(gross - net - sum(breakdown))
    deductions = sum(breakdown)+rounding
    if money(gross-deductions)!=net: raise ValueError('Reconciliation difference detected')
    return dict(zip(HEADERS,[report_date,settlement_date,ref,count,upi,card,
                            fmt(gross),fmt(breakdown[0]),fmt(breakdown[1]),fmt(breakdown[2]),
                            fmt(breakdown[3]),fmt(breakdown[4]),fmt(rounding),fmt(deductions),fmt(net),Path(name).name]))

def load_summary(data,name):
    rows, fields=csv_rows(data,name)
    if not set(HEADERS).issubset(fields): raise ValueError('Not a supported consolidated summary')
    result=[]
    for r in rows:
        if r['Report Date'].strip().upper()=='GRAND TOTAL': continue
        if not (r['Bank Reference'] or '').strip(): continue
        for col in AMOUNTS: amt(r[col])
        if money(amt(r['Gross Amount'])-amt(r['Total Deductions'])) != money(amt(r['Net Bank Entry'])):
            raise ValueError('Summary contains an unbalanced row: '+r['Bank Reference'])
        result.append({k:r[k] for k in HEADERS})
    return result

def parse_files(paths):
    """Return (rows, warnings). Ref collisions with different numbers are refused."""
    entries={}; warnings=[]
    def take(data,name):
        try:
            if name.lower().endswith('.zip'):
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for info in sorted(z.infolist(), key=lambda i:i.filename):
                        if info.is_dir() or not info.filename.lower().endswith('.csv'): continue
                        if info.file_size>10_000_000: warnings.append(f'Skipped oversized CSV: {info.filename}');continue
                        take(z.read(info), Path(info.filename).name)
                return
            if not name.lower().endswith('.csv'): return
            fields = csv_rows(data,name)[1]
            incoming = load_summary(data,name) if 'Net Bank Entry' in fields else [parse_settlement(data,name,warnings)]
            for row in incoming:
                ref=row['Bank Reference']
                if ref in entries:
                    prior=entries[ref]
                    if any(amt(row[k])!=amt(prior[k]) for k in HEADERS[3:12] + HEADERS[13:15]):
                        warnings.append(f'CONFLICT: {ref} in {name} differs from existing entry; kept first')
                    else: warnings.append(f'Duplicate skipped: {ref} ({name})')
                else: entries[ref]=row
        except (ValueError,UnicodeError,zipfile.BadZipFile,KeyError,csv.Error) as ex:
            warnings.append(f'ERROR {name}: {ex}')
    for p in paths:
        with open(p,'rb') as f:take(f.read(),os.path.basename(p))
    def sortkey(row):return datetime.strptime(row['Report Date'],'%d-%m-%Y'),row['Bank Reference']
    return sorted(entries.values(),key=sortkey),warnings

def settlement_overview(rows, warnings=None):
    """Flag missing REPORT dates. Scan whole first calendar month, up to last observed report in final month."""
    if not rows:
        return {'input_reports':0, 'payment_transactions':0, 'upi':0, 'card':0,
                'gross':Decimal(0),'processing':Decimal(0),'processing_gst':Decimal(0),
                'rental':Decimal(0),'rental_gst':Decimal(0),'other':Decimal(0),
                'rounding':Decimal(0),'deductions':Decimal(0),'net':Decimal(0),
                'missing':[], 'period':'No reports'}
    dates=sorted(datetime.strptime(r['Report Date'],'%d-%m-%Y').date() for r in rows)
    present=set(dates)
    start=dates[0].replace(day=1)
    end=dates[-1]
    missing=[]
    cursor=start
    while cursor<=end:
        if cursor.weekday()!=6 and cursor not in present:
            missing.append(cursor)
        cursor+=timedelta(days=1)
    mapping={'gross':'Gross Amount','processing':'Processing Fee','processing_gst':'GST on Processing Fee',
             'rental':'Machine Rental','rental_gst':'GST on Machine Rental',
             'other':'Other Deductions','rounding':'Rounding Adjustment',
             'deductions':'Total Deductions','net':'Net Bank Entry'}
    result={k:sum((amt(r[v]) for r in rows),Decimal(0)) for k,v in mapping.items()}
    result.update(input_reports=len(rows),payment_transactions=sum(int(r['Transactions']) for r in rows),
                  upi=sum(int(r['UPI']) for r in rows),card=sum(int(r['Card']) for r in rows),
                  missing=missing,period=f'{start:%d-%m-%Y} to {end:%d-%m-%Y}')
    return result

def export_csv(rows,output):
    if not rows: raise ValueError('No settlement rows available')
    total = {k:sum((amt(r[k]) for r in rows), Decimal(0)) for k in AMOUNTS}
    for col in ('Transactions','UPI','Card'): total[col]=sum(int(r[col]) for r in rows)
    grand = {k:'' for k in HEADERS};grand['Report Date']='GRAND TOTAL'
    for k in AMOUNTS:grand[k]=fmt(total[k])
    for k in ('Transactions','UPI','Card'):grand[k]=total[k]
    if money(total['Gross Amount']-total['Total Deductions'])!=money(total['Net Bank Entry']):
        raise ValueError('Grand total failed reconciliation')
    with open(output,'w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=HEADERS)
        writer.writeheader();writer.writerows(rows);writer.writerow(grand)
    return grand

def export_xlsx(rows, output):
    """Write a genuine Excel .xlsx file with stdlib only (no pip installation)."""
    from xml.sax.saxutils import escape
    from zipfile import ZipFile, ZIP_DEFLATED

    if not rows: raise ValueError('No settlement rows available')
    total = {k:sum((amt(r[k]) for r in rows), Decimal(0)) for k in AMOUNTS}
    for col in ('Transactions','UPI','Card'): total[col]=sum(int(r[col]) for r in rows)
    grand = {k:'' for k in HEADERS}
    grand['Report Date']='GRAND TOTAL'
    for k in AMOUNTS: grand[k]=fmt(total[k])
    for k in ('Transactions','UPI','Card'): grand[k]=total[k]
    if money(total['Gross Amount']-total['Total Deductions'])!=money(total['Net Bank Entry']):
        raise ValueError('Grand total failed reconciliation')

    def xl_col(i):
        result=''
        while i:
            i, rem=divmod(i-1,26)
            result=chr(65+rem)+result
        return result

    def cell(row_num, col_num, value, header=False, grand_total=False):
        cell_ref=f'{xl_col(col_num)}{row_num}'
        style='1' if header else ('2' if grand_total and col_num>=4 else ('3' if col_num in range(7,16) else ('2' if grand_total else '0')))
        if not header and col_num in range(4,16) and value not in ('',None):
            # Counts remain numeric integers, all money fields are numeric with two decimals.
            v=str(int(value)) if col_num<=6 else fmt(amt(value))
            return f'<c r="{cell_ref}" s="{style}"><v>{v}</v></c>'
        if value is None or value=='': return ''
        # Strings are explicitly stored as inline strings, not formula expressions.
        val=escape(str(value)).replace('\r','')
        return f'<c r="{cell_ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{val}</t></is></c>'

    lines=[]
    for row_idx, record in enumerate([dict(zip(HEADERS,HEADERS))]+list(rows)+[grand],1):
        cells=''.join(cell(row_idx,i,record.get(key,''),header=(row_idx==1),grand_total=(row_idx==len(rows)+2)) for i,key in enumerate(HEADERS,1))
        lines.append(f'<row r="{row_idx}"{' ht="26" customHeight="1"' if row_idx==1 else ''}>{cells}</row>')
    widths=[16,25,23,15,10,10,18,18,24,19,24,20,23,19,19,58]
    columns=''.join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i,width in enumerate(widths,1))
    last_row=len(rows)+2
    sheet=(f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
           f'<cols>{columns}</cols><sheetData>{"".join(lines)}</sheetData>'
           f'<autoFilter ref="A1:P{last_row-1}"/></worksheet>')
    styles=('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
      '<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00;[Red](#,##0.00)"/></numFmts>'
      '<fonts count="3"><font><sz val="10"/><name val="Aptos"/></font>'
      '<font><b/><color rgb="FFFFFFFF"/><sz val="10"/><name val="Aptos"/></font>'
      '<font><b/><color rgb="FF102A43"/><sz val="10"/><name val="Aptos"/></font></fonts>'
      '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
      '<fill><patternFill patternType="solid"><fgColor rgb="FF17365D"/><bgColor indexed="64"/></patternFill></fill>'
      '<fill><patternFill patternType="solid"><fgColor rgb="FFEAF1F7"/><bgColor indexed="64"/></patternFill></fill></fills>'
      '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
      '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
      '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
      '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1"/>'
      '<xf numFmtId="0" fontId="2" fillId="3" borderId="0" xfId="0" applyFill="1" applyFont="1"/>'
      '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
      '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    content_types=('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
       '<Default Extension="xml" ContentType="application/xml"/>'
       '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
       '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
       '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
       '</Types>')
    overview=settlement_overview(rows)
    overview_items=[
        ('PHONEPE SETTLEMENT OVERVIEW',''),
        ('Period checked',overview['period']),
        ('Basis','Report Date (CSV filename); Sundays excluded'),
        ('Coverage note','First month starts on the 1st; final month ends on latest report date'),
        ('Missing-date note','Potentially missing reports; no-transaction days may be legitimate'),
        ('',''),
        ('Unique settlement reports',overview['input_reports']),
        ('Payment transactions',overview['payment_transactions']),
        ('UPI payments',overview['upi']),
        ('Card payments',overview['card']),
        ('',''),
        ('Gross collections',overview['gross']),
        ('Processing fees',overview['processing']),
        ('GST on processing fees',overview['processing_gst']),
        ('Machine rental',overview['rental']),
        ('GST on machine rental',overview['rental_gst']),
        ('Other deductions',overview['other']),
        ('Rounding adjustment',overview['rounding']),
        ('TOTAL DEDUCTIONS',overview['deductions']),
        ('NET BANK SETTLEMENTS',overview['net']),
        ('CHECK (should equal zero)',money(overview['gross']-overview['deductions']-overview['net'])),
        ('',''),
        ('POTENTIALLY MISSING REPORT DATES',len(overview['missing']))]
    for d in overview['missing']:
        overview_items.append((d.strftime('%d-%m-%Y'), d.strftime('%A')))
    if not overview['missing']:
        overview_items.append(('None within checked range',''))
    def ovcell(rowno,colno,val):
        ref=f'{xl_col(colno)}{rowno}'
        if isinstance(val,Decimal):
            return f'<c r="{ref}" s="3"><v>{fmt(val)}</v></c>'
        if isinstance(val,int):
            return f'<c r="{ref}"><v>{val}</v></c>'
        if not val: return ''
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(val))}</t></is></c>'
    overview_lines=[]
    for i,(key,value) in enumerate(overview_items,1):
        overview_lines.append(f'<row r="{i}">{ovcell(i,1,key)}{ovcell(i,2,value)}</row>')
    overview_sheet=('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<cols><col min="1" max="1" width="44" customWidth="1"/><col min="2" max="2" width="83" customWidth="1"/></cols>'
        f'<sheetData>{"".join(overview_lines)}</sheetData></worksheet>')
    content_types=content_types.replace('</Types>',
        '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    with ZipFile(output,'w',ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',content_types)
        z.writestr('_rels/.rels','<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml','<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Settlements" sheetId="1" r:id="rId1"/><sheet name="Overview" sheetId="2" r:id="rId3"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
        z.writestr('xl/styles.xml',styles)
        z.writestr('xl/worksheets/sheet1.xml',sheet)
        z.writestr('xl/worksheets/sheet2.xml',overview_sheet)
    return grand

def launch_gui():
    """SettAx desktop GUI. CSV/accounting engine is deliberately kept separate."""
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, font as tkfont
    import json

    settings_dir = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'SettAx'
    settings_file = settings_dir / 'settings.json'
    try:
        prefs = json.loads(settings_file.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        prefs = {}
    root = tk.Tk()
    root.title('SettAx  |  v1.7')
    root.geometry('1240x760')
    root.minsize(960, 610)
    try: root.tk.call('tk','scaling',1.0)
    except tk.TclError: pass
    theme_var=tk.StringVar(master=root,value=prefs.get('theme','Light') if prefs.get('theme') in ('Light','Dark') else 'Light')
    fonts={
        'title':tkfont.Font(root=root,family='Segoe UI',size=16,weight='bold'),
        'heading':tkfont.Font(root=root,family='Segoe UI',size=12,weight='bold'),
        'body':tkfont.Font(root=root,family='Segoe UI',size=10),
        'small':tkfont.Font(root=root,family='Segoe UI',size=10),
        'number':tkfont.Font(root=root,family='Segoe UI',size=16,weight='bold'),
    }
    themes={
      'Light':dict(bg='#f3f6fa',surface='#ffffff',input='#ffffff',text='#1b2b42',muted='#586b82',navy='#23466d',accent='#17738b',line='#d6e0eb',head='#e9f0f8',alt='#f7f9fc',select='#d5e9f5',warn='#9b5c16',good='#167452'),
      'Dark':dict(bg='#121b28',surface='#1b2838',input='#25364b',text='#e8eff7',muted='#adc0d3',navy='#57aed0',accent='#66c4df',line='#36495f',head='#26384c',alt='#213145',select='#375571',warn='#ffc27a',good='#8cdec2')
    }
    palette=themes[theme_var.get()]
    root.option_add('*Font',fonts['body'])
    style=ttk.Style(root)
    style.theme_use('clam')
    sources=[]; current=[]; notices=[]
    filter_var=tk.StringVar(master=root)
    status_var=tk.StringVar(master=root,value='Add ZIP or CSV reports to begin.')
    period_var=tk.StringVar(master=root,value='No settlements loaded')
    report_stat=tk.StringVar(master=root,value='0 reports    ·    0 payments    ·    0 UPI    ·    0 card')
    fee_stat=tk.StringVar(master=root,value='Processing ₹0.00   ·   GST ₹0.00   ·   Rental ₹0.00   ·   Rental GST ₹0.00   ·   Other ₹0.00   ·   Rounding ₹0.00')
    missing_stat=tk.StringVar(master=root,value='0 dates to review')
    notice_stat=tk.StringVar(master=root,value='No validation issues')
    metrics={x:tk.StringVar(master=root,value='₹0.00') for x in ('gross','deductions','net')}
    containers=[]; labels=[]; text_fields=[]
    def panel(parent,role='surface',**kwargs):
        w=tk.Frame(parent,**kwargs);containers.append((w,role));return w
    def label(parent,value=None,var=None,kind='body',role='text',**kwargs):
        kw=dict(textvariable=var) if var is not None else dict(text=value)
        w=tk.Label(parent,font=fonts[kind],**kw,**kwargs);labels.append((w,role));return w

    main=panel(root,'bg');main.pack(fill='both',expand=True,padx=17,pady=12)
    heading=panel(main,'bg');heading.pack(fill='x',pady=(0,10))
    label(heading,'SettAx',kind='title').pack(side='left')
    label(heading,'MERCHANT SETTLEMENT WORKSPACE  ·  v1.7',kind='small',role='muted').pack(side='left',padx=13,pady=(4,0))
    theme_button=ttk.Button(heading,text='◐  Switch theme',command=lambda:toggle_theme())
    theme_button.pack(side='right')

    actions=panel(main,'bg');actions.pack(fill='x',pady=(0,10))
    btn_add=ttk.Button(actions,text='＋  Import ZIP / CSV',style='Accent.TButton');btn_add.pack(side='left',padx=(0,7))
    btn_export=ttk.Button(actions,text='Export Excel',command=lambda:export('xlsx'));btn_export.pack(side='left',padx=(0,7))
    btn_clear=ttk.Button(actions,text='Clear session');btn_clear.pack(side='left')
    label(actions,var=period_var,kind='small',role='muted').pack(side='right')

    cards=panel(main,'bg');cards.pack(fill='x',pady=(0,9))
    card_items=[('gross','GROSS COLLECTIONS'),('deductions','TOTAL DEDUCTIONS'),('net','NET BANK SETTLEMENTS')]
    for i,(key,caption) in enumerate(card_items):
        cards.grid_columnconfigure(i,weight=1,uniform='metrics')
        card=panel(cards,'surface',highlightthickness=1);card.grid(row=0,column=i,sticky='ew',padx=(0,8) if i<2 else 0)
        label(card,caption,kind='small',role='muted').pack(anchor='w',padx=12,pady=(9,1))
        label(card,var=metrics[key],kind='number',role='warn' if key=='deductions' else 'navy').pack(anchor='w',padx=12,pady=(0,9))

    summaries=panel(main,'bg');summaries.pack(fill='x',pady=(0,9))
    summaries.grid_columnconfigure(0,weight=3,uniform='summary'); summaries.grid_columnconfigure(1,weight=2,uniform='summary')
    statbox=panel(summaries,'surface',highlightthickness=1);statbox.grid(row=0,column=0,sticky='nsew',padx=(0,8))
    label(statbox,var=report_stat,kind='heading').pack(fill='x',anchor='w',padx=12,pady=(9,2))
    label(statbox,var=fee_stat,kind='small',role='muted',anchor='w',justify='left',wraplength=640).pack(fill='x',padx=12,pady=(0,9))
    datebox=panel(summaries,'surface',highlightthickness=1);datebox.grid(row=0,column=1,sticky='nsew')
    datehead=panel(datebox,'surface');datehead.pack(fill='x',padx=12,pady=(7,1))
    label(datehead,'Date coverage',kind='heading').pack(side='left')
    label(datehead,var=missing_stat,kind='small',role='warn').pack(side='right')
    dates_text=tk.Text(datebox,height=2,wrap='word',font=fonts['small'],relief='flat',bd=0,padx=7,pady=5,cursor='arrow',state='normal')
    dates_text.pack(fill='both',expand=True,padx=12,pady=(2,8))
    dates_text.insert('1.0','No reports loaded. Sundays excluded from missing-date checks.')
    dates_text.configure(state='disabled')
    text_fields.append(dates_text)

    bar=panel(main,'bg');bar.pack(fill='x',pady=(0,6))
    label(bar,'Settlement entries',kind='heading').pack(side='left')
    search_entry=ttk.Entry(bar,textvariable=filter_var,width=28);search_entry.pack(side='right')
    label(bar,'Find:',kind='small',role='muted').pack(side='right',padx=(0,7))

    tablebox=panel(main,'surface',highlightthickness=1);tablebox.pack(fill='both',expand=True)
    columns=('Report Date','Settlement Date','Bank Reference','Payments','Gross','Deductions','Net Bank')
    tree=ttk.Treeview(tablebox,columns=columns,show='headings',selectmode='extended')
    for col,width in zip(columns,(125,130,240,95,140,145,150)):
        tree.heading(col,text=col)
        tree.column(col,width=width,minwidth=78,anchor='e' if col in columns[3:] else 'w',stretch=True)
    vscroll=ttk.Scrollbar(tablebox,orient='vertical',command=tree.yview)
    hscroll=ttk.Scrollbar(tablebox,orient='horizontal',command=tree.xview)
    tree.configure(yscrollcommand=vscroll.set,xscrollcommand=hscroll.set)
    vscroll.pack(side='right',fill='y');hscroll.pack(side='bottom',fill='x');tree.pack(side='left',fill='both',expand=True)

    footer=panel(main,'bg');footer.pack(fill='x',pady=(8,0))
    label(footer,var=status_var,kind='small',role='muted').pack(side='left')
    ttk.Button(footer,text='Validation notices',command=lambda:show_notices()).pack(side='right')
    label(footer,var=notice_stat,kind='small',role='muted').pack(side='right',padx=(0,9))

    menu=tk.Menu(root,tearoff=False)
    filemenu=tk.Menu(menu,tearoff=False)
    editmenu=tk.Menu(menu,tearoff=False)
    viewmenu=tk.Menu(menu,tearoff=False)
    helpmenu=tk.Menu(menu,tearoff=False)
    menu.add_cascade(label='File',menu=filemenu)
    menu.add_cascade(label='Edit',menu=editmenu)
    menu.add_cascade(label='View',menu=viewmenu)
    menu.add_cascade(label='Help',menu=helpmenu)
    filemenu.add_command(label='Import ZIP / CSV...',accelerator='Ctrl+O',command=lambda:add_files())
    filemenu.add_command(label='Export Excel...',accelerator='Ctrl+E',command=lambda:export('xlsx'))
    filemenu.add_command(label='Export CSV...',command=lambda:export('csv'))
    filemenu.add_separator()
    filemenu.add_command(label='Clear session',command=lambda:clear_all())
    filemenu.add_separator()
    filemenu.add_command(label='Exit',accelerator='Alt+F4',command=lambda:close_app())
    editmenu.add_command(label='Cut',accelerator='Ctrl+X',command=lambda:edit_action('cut'))
    editmenu.add_command(label='Copy',accelerator='Ctrl+C',command=lambda:edit_action('copy'))
    editmenu.add_command(label='Paste',accelerator='Ctrl+V',command=lambda:edit_action('paste'))
    editmenu.add_separator()
    editmenu.add_command(label='Select all',accelerator='Ctrl+A',command=lambda:edit_action('all'))
    editmenu.add_command(label='Find...',accelerator='Ctrl+F',command=lambda:focus_find())
    viewmenu.add_radiobutton(label='Light theme',variable=theme_var,value='Light',command=lambda:apply_theme())
    viewmenu.add_radiobutton(label='Dark theme',variable=theme_var,value='Dark',command=lambda:apply_theme())
    viewmenu.add_separator()
    viewmenu.add_command(label='Validation notices',command=lambda:show_notices())
    helpmenu.add_command(label='User guide',command=lambda:show_guide())
    helpmenu.add_command(label='About SettAx',command=lambda:about())
    root.configure(menu=menu)

    def show_rows(*_):
        query=filter_var.get().strip().lower()
        tree.delete(*tree.get_children())
        for i,r in enumerate(current):
            found=' '.join(str(r[k]) for k in ('Report Date','Settlement Date (from CSV)','Bank Reference','Source CSV')).lower()
            if query and query not in found: continue
            tree.insert('','end',values=(r['Report Date'],r['Settlement Date (from CSV)'],r['Bank Reference'],
                    r['Transactions'],r['Gross Amount'],r['Total Deductions'],r['Net Bank Entry']),tags=('alternate',) if i%2 else ())
    filter_var.trace_add('write',show_rows)

    def refresh():
        nonlocal current,notices
        current,notices=parse_files(sources)
        show_rows()
        s=settlement_overview(current)
        for k in ('gross','deductions','net'): metrics[k].set('₹'+fmt(s[k]))
        report_stat.set(f'{len(current)} reports     ·     {s["payment_transactions"]} payments     ·     {s["upi"]} UPI     ·     {s["card"]} card')
        fee_stat.set('Processing ₹'+fmt(s['processing'])+'   ·   GST ₹'+fmt(s['processing_gst'])+
                     '   ·   Rental ₹'+fmt(s['rental'])+'   ·   Rental GST ₹'+fmt(s['rental_gst'])+
                     '   ·   Other ₹'+fmt(s['other'])+'   ·   Rounding ₹'+fmt(s['rounding']))
        period_var.set(s['period'])
        missing_stat.set(f'{len(s["missing"])} potentially missing')
        dates_text.configure(state='normal');dates_text.delete('1.0','end')
        dates_text.insert('1.0',',  '.join(d.strftime('%d %b %Y (%a)') for d in s['missing']) if s['missing'] else 'No potentially missing non-Sunday report dates.')
        dates_text.configure(state='disabled')
        notice_stat.set(f'{len(notices)} notices' if notices else 'No validation issues')
        status_var.set(f'{len(sources)} input files   ·   {len(current)} unique settlements   ·   {len(notices)} notices')

    def add_files():
        paths=filedialog.askopenfilenames(title='Import PhonePe CSV, ZIP or earlier CSV summary',filetypes=[('PhonePe files','*.csv *.zip'),('All files','*.*')])
        if not paths:return
        for p in paths:
            if p not in sources:sources.append(p)
        refresh()

    def clear_all():
        if sources and not messagebox.askyesno('Clear session','Clear imported reports from memory?\nOriginal files on disk are not deleted.',parent=root):return
        sources.clear();refresh()

    def export(format_name):
        if not current: messagebox.showwarning('No data','Import settlement reports first.',parent=root);return
        if any(w.startswith(('ERROR','CONFLICT')) for w in notices):
            if not messagebox.askyesno('Validation notices','Some files have errors or conflicting entries. Export the valid rows only?',parent=root):return
        ext='.xlsx' if format_name=='xlsx' else '.csv'
        filetypes=[('Excel workbook','*.xlsx')] if format_name=='xlsx' else [('CSV file','*.csv')]
        target=filedialog.asksaveasfilename(title='Export settlements',defaultextension=ext,
                initialfile='SettAx_Consolidated_Settlements'+ext,filetypes=filetypes)
        if not target:return
        try:
            if format_name=='xlsx':export_xlsx(current,target)
            else:export_csv(current,target)
            status_var.set('Exported '+os.path.basename(target))
            messagebox.showinfo('Export complete',f'Exported {len(current)} settlement reports.\n{target}',parent=root)
        except Exception as ex:messagebox.showerror('Export failed',str(ex),parent=root)

    def focus_find():search_entry.focus_set();search_entry.selection_range(0,'end')
    def edit_action(action):
        widget=root.focus_get()
        if widget is None:return
        if widget is tree:
            if action=='copy':
                selected=tree.selection()
                if selected:
                    table_text='\n'.join('\t'.join(str(x) for x in tree.item(item,'values')) for item in selected)
                    root.clipboard_clear();root.clipboard_append(table_text)
                    status_var.set(f'Copied {len(selected)} settlement row(s)')
            elif action=='all':tree.selection_set(tree.get_children())
            # Financial rows intentionally cannot be cut or pasted.
            return
        if isinstance(widget,(tk.Entry,ttk.Entry,tk.Text,ttk.Combobox)):
            event={'cut':'<<Cut>>','copy':'<<Copy>>','paste':'<<Paste>>','all':'<<SelectAll>>'}[action]
            try:
                if action=='all' and not isinstance(widget,tk.Text):widget.selection_range(0,'end')
                elif action=='all':widget.tag_add('sel','1.0','end-1c')
                else:widget.event_generate(event)
            except tk.TclError:pass

    def show_notices():
        pop=tk.Toplevel(root);pop.title('SettAx  ·  Validation notices');pop.geometry('720x350')
        area=tk.Text(pop,wrap='word',font=fonts['body'],padx=12,pady=10)
        area.pack(fill='both',expand=True)
        area.insert('1.0','\n'.join(notices) if notices else 'No validation issues.')
        area.configure(state='disabled')
        text_fields.append(area)

    def about():
        messagebox.showinfo('About SettAx',
            'SettAx 1.6\nOffline merchant settlement consolidator\n\n'
            'Imports PhonePe CSV/ZIP files, reconciles daily settlements, and exports Excel/CSV.\n'
            'Includes missing report-date checks (Sundays excluded).\n\n'
            'Local processing only · No online connection · No background tasks',parent=root)

    def show_guide():
        messagebox.showinfo('SettAx · Quick guide',
            '1. File → Import ZIP / CSV to add settlement reports.\n'
            '2. Use Find (Ctrl+F) to filter by date, reference or filename.\n'
            '3. Review validation notices and potential missing non-Sunday report dates.\n'
            '4. File → Export Excel (Ctrl+E) for a two-sheet workbook.\n'
            '5. File → Export CSV for interchange.\n\n'
            'Duplicate references are skipped; conflicting data is flagged.\n'
            'Settlement rows are read-only. Ctrl+C copies selected rows.',parent=root)

    def apply_theme():
        nonlocal palette
        palette=themes[theme_var.get()]
        p=palette
        root.configure(bg=p['bg'])
        for widget,role in containers:
            try:widget.configure(bg=p[role],highlightbackground=p['line'])
            except tk.TclError:pass
        for widget,role in labels:
            try:widget.configure(bg=widget.master.cget('bg'),fg=p[role])
            except tk.TclError:pass
        for widget in text_fields:
            try:widget.configure(bg=p['input'],fg=p['text'],insertbackground=p['text'],selectbackground=p['select'])
            except tk.TclError:pass
        style.configure('.',font=fonts['body'],background=p['bg'],foreground=p['text'])
        style.configure('TButton',font=fonts['body'],padding=(10,6),background=p['surface'],foreground=p['text'],bordercolor=p['line'])
        style.map('TButton',background=[('active',p['head'])],foreground=[('active',p['text'])])
        style.configure('Accent.TButton',background=p['navy'] if theme_var.get()=='Light' else '#287d99',foreground='#ffffff')
        style.map('Accent.TButton',background=[('active',p['accent'])],foreground=[('active','#ffffff')])
        style.configure('TEntry',fieldbackground=p['input'],foreground=p['text'],insertcolor=p['text'],padding=5)
        style.configure('Treeview',background=p['surface'],fieldbackground=p['surface'],foreground=p['text'],rowheight=29,borderwidth=0,font=fonts['body'])
        style.map('Treeview',background=[('selected',p['select'])],foreground=[('selected',p['text'])])
        style.configure('Treeview.Heading',background=p['head'],foreground=p['text'],font=fonts['heading'],relief='flat',padding=(7,7))
        style.map('Treeview.Heading',background=[('active',p['line'])])
        style.configure('Vertical.TScrollbar',background=p['head'],troughcolor=p['bg'])
        style.configure('Horizontal.TScrollbar',background=p['head'],troughcolor=p['bg'])
        tree.tag_configure('alternate',background=p['alt'])
        try:
            menu.configure(bg=p['surface'],fg=p['text'],activebackground=p['select'],activeforeground=p['text'])
            for m in (filemenu,editmenu,viewmenu,helpmenu):m.configure(bg=p['surface'],fg=p['text'],activebackground=p['select'],activeforeground=p['text'])
        except tk.TclError:pass
        status_var.set(f'{theme_var.get()} theme enabled · {len(current)} settlements loaded')

    def toggle_theme():theme_var.set('Dark' if theme_var.get()=='Light' else 'Light');apply_theme()

    def close_app():
        # No network, database or background workers are created. Files are read via
        # context managers and exports close after writing. Free UI/session references.
        try:
            settings_dir.mkdir(parents=True,exist_ok=True)
            settings_file.write_text(json.dumps({'theme':theme_var.get()}),encoding='utf-8')
        except OSError:pass
        sources.clear();current.clear();notices.clear()
        root.quit();root.destroy()

    btn_add.configure(command=add_files);btn_clear.configure(command=clear_all)
    root.bind('<Control-o>',lambda e:add_files())
    root.bind('<Control-e>',lambda e:export('xlsx'))
    root.bind('<Control-f>',lambda e:focus_find())
    # Native text Entry/Text widgets already provide Ctrl+C/X/V; only intercept the table.
    root.bind('<Control-c>',lambda e:('break' if root.focus_get() is tree and not edit_action('copy') else None))
    root.bind('<Control-a>',lambda e:('break' if root.focus_get() is tree and not edit_action('all') else None))
    root.protocol('WM_DELETE_WINDOW',close_app)
    apply_theme()
    root.mainloop()

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--cli':
        if len(sys.argv)<4:
            print('Usage: python phonepe_settlement_tool.py --cli OUTPUT.xlsx INPUT.csv [MORE.csv|REPORTS.zip ...]');sys.exit(2)
        records,issues=parse_files(sys.argv[3:]);totals=(export_xlsx(records,sys.argv[2]) if sys.argv[2].lower().endswith('.xlsx') else export_csv(records,sys.argv[2]));print('Reports:',len(records),'Gross:',totals['Gross Amount'],'Deductions:',totals['Total Deductions'],'Net:',totals['Net Bank Entry']);print('Missing report dates (excluding Sundays):',', '.join(d.strftime('%d-%m-%Y') for d in settlement_overview(records)['missing']));print('\n'.join(issues))
        if any(w.startswith(('ERROR','CONFLICT')) for w in issues):sys.exit(1)
    else:launch_gui()

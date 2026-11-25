import importlib.util,glob,csv,io,tempfile,os,zipfile
from decimal import Decimal
p=str(__import__('pathlib').Path(__file__).with_name('phonepe_settlement_tool.py'))
s=importlib.util.spec_from_file_location('settax',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
cols=['PaymentType','MerchantReferenceId','PhonePeReferenceId','From','Instrument','CreationDate','TransactionDate','SettlementDate','BankReferenceNo','Amount','Fee','IGST','CGST','SGST']
def make(items,ref):
 f=io.StringIO(); cw=csv.DictWriter(f,fieldnames=cols);cw.writeheader()
 for i,item in enumerate(items):
  default=dict(PaymentType='PAYMENT',MerchantReferenceId='m'+str(i),PhonePeReferenceId='p'+str(i),From='PhonePe',Instrument='UPI_FULFILMENT',CreationDate='29-10-2026',TransactionDate='29-10-2026',SettlementDate='30-10-2026',BankReferenceNo=ref,Amount='0',Fee='0',IGST='0',CGST='0',SGST='0');default.update(item);cw.writerow(default)
 return f.getvalue().encode()
wallet=make([dict(Instrument='WALLET_FULFILMENT',Amount='1000',Fee='-20',IGST='-3.60'),dict(Amount='500')],'AXNPN30364019770')
vas=make([dict(PaymentType='REFUND',Instrument='VAS_RECURRING_CHARGE_COLLECTION',Amount='0',Fee='-40',IGST='-7.20'),dict(Amount='1000')],'AXNPN27544398804')
for name,data,expect in [('wallet',wallet,(Decimal('1500'),Decimal('23.60'),Decimal('1476.40'))),('vas',vas,(Decimal('1000'),Decimal('47.20'),Decimal('952.80')))]:
 warnings=[];r=m.parse_settlement(data,name+'.csv',warnings);got=tuple(Decimal(r[k]) for k in ('Gross Amount','Total Deductions','Net Bank Entry'));print(name,got,'warnings',warnings)
 assert got==expect and not warnings
mix=make([dict(Amount='120'),dict(PaymentType='REFUND',Instrument='VAS_RECURRING_CHARGE_COLLECTION',Amount='-20'),dict(PaymentType='REFUND',Instrument='VAS_RECURRING_CHARGE_COLLECTION',Amount='10'),dict(PaymentType='ADJUSTMENT',Instrument='NEW_FEE',Fee='-5',IGST='-0.90')],'AXNPN999')
w=[];r=m.parse_settlement(mix,'mix.csv',w);print('mixed',r['Gross Amount'],r['Other Deductions'],r['Net Bank Entry'],w)
assert (r['Gross Amount'],r['Other Deductions'],r['Net Bank Entry'])==('120.00','15.90','104.10')
with tempfile.TemporaryDirectory() as d:
 for name,data in [('a.csv',wallet),('b.csv',vas)]:open(os.path.join(d,name),'wb').write(data)
 rr,ww=m.parse_files([os.path.join(d,'a.csv'),os.path.join(d,'b.csv')]);print('combined',len(rr),ww)
 path=os.path.join(d,'result.xlsx');m.export_xlsx(rr,path)
 with zipfile.ZipFile(path) as z: print('xlsx parts',len(z.namelist()))
print('PASS')

SETTAX v1.7 — OFFLINE PHONEPE RECONCILIATION
================================================

WHAT'S NEW
- A conventional File / Edit / View / Help menu bar.
- File: Import ZIP/CSV, Export Excel, Export CSV, Clear Session, Exit.
- Edit: Cut / Copy / Paste / Select All for editable fields; Copy selected
  settlement rows (tab-separated for pasting into Excel). Accounting rows are
  intentionally read-only; cutting or pasting them is not supported.
- Find (Ctrl+F) filters settlement dates, references and source file names.
- View: Light/Dark mode; selection is remembered locally between launches.
- Help: built-in quick guide and About SettAx.
- Cleaner close: releases the Tk window and in-memory report references;
  all input/output files already use short-lived file handles. No network,
  persistent database, or background workers exist to disconnect.
- Existing reconciliation calculations, missing-date checks, and two-sheet
  Excel export remain unchanged.

SHORTCUTS
Ctrl+O Import ZIP/CSV; Ctrl+E Export Excel; Ctrl+F Find; Ctrl+C copy
selected settlement rows or text; Ctrl+X / Ctrl+V edit selected text;
Ctrl+A select all records when the table is focused; Alt+F4 close.

BUILD A SINGLE WINDOWS EXE
1. Extract the ZIP to a local folder.
2. Double-click Build_Windows_EXE.bat; by default it uses C:\Python314\python.exe.
3. After building, use dist\SettAx.exe (a single-file portable executable).
4. Close the previous SettAx version before replacing its executable.
5. The build script installs PyInstaller in a project-specific virtual environment.

OPTIONAL INSTALLER / UNINSTALL
Install Inno Setup, compile SettAx.iss AFTER building dist\SettAx.exe.
This creates a normal Windows installed app and uninstall entry.
Portable EXE users can update by replacing only SettAx.exe.

USER DATA
- All processing is offline; importing does not modify originals.
- Theme preference: %LOCALAPPDATA%\SettAx\settings.json.
- Export destination is selected by the user; application does not delete it.
- Folder deletion can be slow if the compiled EXE remains open or is scanned.

TESTS
- Source syntax check and graphical startup check passed on Linux.
- 23 reference settlements: gross 117200.00, deductions 520.43,
  net 116679.57, 7 potentially missing non-Sunday report dates.
- Native Windows EXE still must be built and tested on your PC.

v1.7 — Settlement classification fix (24 November 2026)
-----------------------------------------------------
- PAYMENT / WALLET_FULFILMENT is accepted as a wallet payment. Any signed
  Fee and IGST/CGST/SGST components reduce or increase the net settlement
  as supplied by PhonePe. It is not reported as a bank-to-wallet transfer.
- REFUND / VAS_RECURRING_CHARGE_COLLECTION is accepted as a settlement
  adjustment, with its signed components reflected in Other Deductions.
- Unknown adjustment labels no longer cause the entire CSV report to be
  rejected. A review notice is shown while its signed values are included.
- Positive adjustments reverse previous deductions; negative amounts/fees
  increase deductions. Machine rental remains in its existing separate columns.
- One bank settlement row is still created per source CSV. Excel Overview,
  dark/light themes, import/export, and existing menus remain unchanged.
- Wallet payment counts are included in Transactions, but not UPI or Card.
  The difference between Transactions and UPI+Card may reflect wallet or
  another payment instrument.

WARNING: The exact October 2 and October 30 CSV files were not supplied
with this build. Those scenarios were validated with representative test
rows; verify the updated totals against the original PhonePe settlement and
bank statement after importing the actual files.

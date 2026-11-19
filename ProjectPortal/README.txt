KITTING PORTAL

EASIEST (just this PC): pip install flask pandas openpyxl, then double-click launcher.pyw
  - First run asks for your Excel folder and an admin login, then opens the browser page
  - Keep the small window open while you use it; closing it stops the portal.

FULL SETUP / SHARING WITH COWORKERS (Windows PowerShell):

1. Extract the zip (do NOT run from inside the zip), then open PowerShell in the ProjectPortal folder.
2. pip install -r requirements.txt
3. Put Sharepoint_OnHand.xlsx and Combined_BOMs.xlsx in the data\ folder, OR point at your synced folder:
       $env:PORTAL_DATA_DIR = "C:\Users\you\...\ProjectPortal\data"
4. python check_setup.py          (diagnoses everything; uses a throwaway database)
5. python manage.py create-user yourname --admin
6. python serve.py                (then open http://localhost:8080 ; coworkers use http://<this-pc-name>:8080)


The database lives in %LOCALAPPDATA%\KittingPortal (outside OneDrive on purpose). Run `python manage.py info` to see paths.
Optional email for kit requests: PORTAL_SMTP_HOST, PORTAL_SMTP_PORT, PORTAL_MAIL_FROM, PORTAL_NOTIFY_EMAILS.
Once behind HTTPS set PORTAL_HTTPS=1.
When IIS/Caddy (HTTPS) sits in front of serve.py, also set PORTAL_BEHIND_PROXY=1 and PORTAL_HOST=127.0.0.1.


MAP: admins open Map > Edit layout to add carts (one at a time or many at once), drag/resize them,
Auto-arrange them, change the map size, and assign projects (click a cart).
Zoom with the -/+/Fit buttons or Ctrl + mouse wheel; drag empty floor to pan; zoomed out, click a cart to zoom in.
Everyone can click a project on the map to see the same details as the Projects tab.

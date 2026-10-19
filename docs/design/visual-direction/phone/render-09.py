from playwright.sync_api import sync_playwright
import os
d=os.getcwd()
with sync_playwright() as p:
    b=p.chromium.launch(executable_path='/usr/bin/google-chrome')
    for n,w in [('09-library-grid',412),('09b-library-select',412),('09c-library-delete-confirm',412),('09-library-360-130',360)]:
        pg=b.new_page(viewport={'width':w,'height':915}); pg.goto(f'file://{d}/{n}.html'); pg.wait_for_timeout(300)
        pg.screenshot(path=f'{n}.png'); pg.close()
    pg=b.new_page(viewport={'width':1760,'height':1000})
    cells=''.join(f'<figure><img src="file://{d}/{n}.png"><figcaption>{n}</figcaption></figure>' for n in ['09-library-grid','09b-library-select','09c-library-delete-confirm','09-library-360-130'])
    open(f'{d}/_09sheet.html','w').write(f'<body style="margin:0;background:#E2D8CA;font:16px system-ui;display:flex;gap:16px;padding:16px;align-items:flex-start">{cells}<style>figure{{margin:0}}img{{display:block;border:1px solid #5E5146}}</style></body>'); pg.goto(f'file://{d}/_09sheet.html'); pg.wait_for_timeout(300)
    pg.screenshot(path='09-library-sheet.png',full_page=True); b.close()

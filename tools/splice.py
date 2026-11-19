import sys
import os
root=os.path.abspath(sys.argv[sys.argv.index('--repo')+1]) if '--repo' in sys.argv else '/home/claude'
app=os.path.join(root,'app')
p=os.path.join(app,'WoWForeverAddons.pyw'); s=open(p,encoding='utf-8').read()
html=open(os.path.join(app,'ui','page.html'),encoding='utf-8').read()
assert '"""' not in html
i=s.index('PAGE_HTML = r"""'); j=s.index('"""', i+len('PAGE_HTML = r"""'))
s=s[:i]+'PAGE_HTML = r"""'+html.rstrip('\n')+'\n'+s[j:]
open(p,'w',encoding='utf-8').write(s)
import subprocess
js=html[html.index('<script>')+8:html.rindex('</script>')]
open('/tmp/page.js','w').write(js)
r=subprocess.run(['node','-e',"const fs=require('fs');try{new Function(fs.readFileSync('/tmp/page.js','utf8'));console.log('spliced, js ok')}catch(e){console.log('JS ERROR',e.message);process.exit(1)}"],capture_output=True,text=True)
print(r.stdout.strip() or r.stderr.strip())

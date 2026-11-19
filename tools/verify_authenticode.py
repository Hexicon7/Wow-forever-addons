"""Minimal Authenticode check for a PE file (no Windows needed):
file digest == signed digest, RSA signature over the signed attributes, and leaf cert issued by the
embedded intermediate. Prints the signer chain."""
import hashlib, struct, sys
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

def tlv(b, i):
    tag = b[i]; l = b[i+1]; j = i+2
    if l & 0x80:
        n = l & 0x7f; l = int.from_bytes(b[j:j+n], 'big'); j += n
    return tag, j, j+l          # tag, value start, value end

def children(b, s, e):
    out = []
    while s < e:
        t, vs, ve = tlv(b, s); out.append((t, s, vs, ve)); s = ve
    return out

d = open(sys.argv[1], 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]; opt = pe + 24
magic = struct.unpack_from('<H', d, opt)[0]
ddir = opt + (96 if magic == 0x10b else 112)
co, cl = struct.unpack_from('<II', d, ddir + 32)
chk = opt + 64
blob = d[co+8:co+cl]
algo = None
_, vs, ve = tlv(blob, 0)                       # ContentInfo
ci = children(blob, vs, ve)
_, _, sds, sde = ci[1]                         # [0] EXPLICIT
_, svs, sve = tlv(blob, sds)                   # SignedData SEQ
sd = children(blob, svs, sve)
cinfo = sd[2]                                  # contentInfo
cc = children(blob, cinfo[2], cinfo[3])
_, _, xs, xe = cc[1]                           # [0] EXPLICIT
_, spvs, spve = tlv(blob, xs)                  # SpcIndirectDataContent SEQ (value)
spc_value = blob[spvs:spve]
certs = [x509.load_der_x509_certificate(blob[s:e]) for (t, s, vs2, e) in children(blob, sd[3][2], sd[3][3])] if sd[3][0] == 0xA0 else []
signer_infos = sd[-1]
si = children(blob, signer_infos[2], signer_infos[3])[0]
sif = children(blob, si[2], si[3])
attrs = next(x for x in sif if x[0] == 0xA0)
sig = next(x for x in sif if x[0] == 0x04)
signed_attrs_der = b'\x31' + blob[attrs[1]+1:attrs[3]]
# messageDigest attribute
md = None
for (t, s, avs, ave) in children(blob, attrs[2], attrs[3]):
    a = children(blob, avs, ave)
    oid = blob[a[0][2]:a[0][3]]
    if oid == bytes.fromhex('2a864886f70d010904'):
        v = children(blob, a[1][2], a[1][3])[0]
        md = blob[v[2]:v[3]]
# the file's Authenticode digest inside SpcIndirectDataContent
spc = children(blob, spvs, spve)
dig = children(blob, spc[1][2], spc[1][3])
file_digest_signed = blob[dig[1][2]:dig[1][3]]
hname = {32: 'sha256', 20: 'sha1'}[len(file_digest_signed)]
h = hashlib.new(hname); h.update(d[:chk]); h.update(d[chk+4:ddir+32]); h.update(d[ddir+40:co]); h.update(d[co+cl:])
ok_file = h.digest() == file_digest_signed
ah = {32: hashes.SHA256(), 20: hashes.SHA1()}[len(md)]
ok_md = hashlib.new(ah.name, spc_value).digest() == md
leaf = next(c for c in certs if 'Code Signing PCA' not in c.subject.rfc4514_string() and 'Root' not in c.subject.rfc4514_string())
leaf.public_key().verify(blob[sig[2]:sig[3]], signed_attrs_der, padding.PKCS1v15(), ah)
issuer = next(c for c in certs if c.subject == leaf.issuer)
issuer.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes, padding.PKCS1v15(), leaf.signature_hash_algorithm)
print("file digest matches signed digest:", ok_file)
print("content digest matches:", ok_md)
print("signature over attributes: valid")
print("signer:", leaf.subject.rfc4514_string())
print("issued by:", issuer.subject.rfc4514_string(), "->", issuer.issuer.rfc4514_string())
sys.exit(0 if ok_file and ok_md else 1)

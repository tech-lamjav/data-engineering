"""Mapa pai → filho dos índices (OCI index / manifest list) de um listing do Artifact Registry.

Entrada do `--indices` do `scripts/simula_cleanup_ar.py`. O listing (`gcloud artifacts docker
images list --include-tags --format=json`) diz QUAIS versões são índice, mas não quem são os
filhos; isso só o manifesto do índice diz, pela API de registry (v2). Os repos dbt têm índices
porque o buildx empurrava attestation até 11/2026: o filho amd64 que o job roda não tem tag, e
só fica porque o pai fica.

Uso:

    python3 scripts/indices_ar.py /tmp/ar-dbt-nba-repo.json > /tmp/indices-dbt-nba.txt

Saída: uma linha `indice <pacote>@<pai> <pacote>@<filho>` por filho. Só leitura (GET de
manifesto). Fail-closed: qualquer erro HTTP, resposta que não é índice ou digest fora do formato
sai com 1 e sem saída parcial, porque um mapa incompleto faria a simulação apagar um filho vivo.
"""
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
ACEITA = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


def eh_indice(media_type):
    return "index" in (media_type or "") or "manifest.list" in (media_type or "")


def url_manifesto(pacote, versao):
    """`us-east1-docker.pkg.dev/proj/repo/img` + `sha256:..` → URL da API v2 do registry."""
    host, caminho = pacote.split("/", 1)
    return f"https://{host}/v2/{caminho}/manifests/{versao}"


def linhas_indice(pacote, versao_pai, manifesto):
    if not eh_indice(manifesto.get("mediaType")):
        raise ValueError(f"{pacote}@{versao_pai} não é índice: {manifesto.get('mediaType')!r}")
    filhos = manifesto.get("manifests") or []
    if not filhos:
        raise ValueError(f"{pacote}@{versao_pai}: índice sem filhos")
    linhas = []
    for m in filhos:
        d = m.get("digest") or ""
        if not DIGEST.match(d):
            raise ValueError(f"{pacote}@{versao_pai}: filho com digest inválido {d!r}")
        linhas.append(f"indice {pacote}@{versao_pai} {pacote}@{d}")
    return linhas


def _token():
    return subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    ).stdout.strip()


def _buscar(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": ACEITA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        listing = json.load(f)
    indices = [x for x in listing if eh_indice((x.get("metadata") or {}).get("mediaType"))]
    linhas = []
    if indices:
        token = _token()
        for x in indices:
            try:
                manifesto = _buscar(url_manifesto(x["package"], x["version"]), token)
                linhas += linhas_indice(x["package"], x["version"], manifesto)
            except (urllib.error.URLError, ValueError, KeyError) as e:
                print(f"ERRO: {x.get('package')}@{x.get('version')}: {e}", file=sys.stderr)
                return 1
    print("\n".join(linhas))
    print(f"{len(indices)} índice(s), {len(linhas)} filho(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

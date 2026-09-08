# 限定公開環境の配置手順（仕様第3章・第12章）

講師の実機確認（iPhone Safari / Android Chrome）と、後続のLINE内ブラウザ確認は
HTTPSを前提とします。そのため、小型VPS1台に Caddy を置いた限定公開環境を用意します。

**このディレクトリの値はすべてダミーです。** 実際のFQDN・許可元IP・認証情報は
利用者が指定し、リポジトリには置かないでください。

## 全体の形

```
インターネット
      │  HTTPS（Caddyが証明書を自動取得）
      ▼
  ┌─────────────────────────────┐
  │ VPS                                                    │
  │  Caddy :443                                            │
  │   └ IP制限 または Basic認証（前段の絞り込み）          │
  │        │ 127.0.0.1:8000                                │
  │        ▼                                               │
  │  web（Uvicorn）── 運営ログイン（内側の認証）          │
  │  worker                                                │
  │  data ボリューム（DB・画像・GLB。公開ディレクトリ外）  │
  └─────────────────────────────┘
```

前段の絞り込みと運営ログインが二重になっているのは意図的です。
片方が破られても、もう片方が残ります。

## 手順

### 1. ユーザーが決めること

| 項目 | 例（ダミー） |
| --- | --- |
| FQDN | `art3d.example.invalid` |
| IP制限の許可元 | `203.0.113.10`、`198.51.100.0/24` |
| Basic認証の利用者名 | `teacher` |
| Basic認証のパスワード | 生成して安全に共有する（このファイルに書かない） |

### 2. VPSにファイアウォールを設定する

**Caddy 以外の経路を閉じます。** 特に 8000 番を外に出さないこと。

```bash
# ufw の例（Debian / Ubuntu）
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp        # SSH。可能なら接続元も絞る
sudo ufw allow 80/tcp        # Caddy の証明書取得に必要
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

Docker は iptables を直接操作するため、`ports` に `0.0.0.0` を書くと
ufw を迂回して公開されることがあります。
`deploy/compose.public.yaml` が `127.0.0.1:8000` に固定しているのはそのためです。
配置後に、外から 8000 番へ**つながらないこと**を必ず確かめてください。

```bash
# 別のネットワークから実行して、接続できないことを確認する
curl -m 5 http://art3d.example.invalid:8000/healthz || echo "外からは届かない（期待どおり）"
```

### 3. Basic認証のハッシュを作る

平文をファイルに書かないでください。

```bash
docker run --rm caddy caddy hash-password --plaintext 'ここに入力'
```

出力されたハッシュを `Caddyfile` に貼ります。

### 4. 配置する

```bash
# リポジトリをVPSへ配置し、.env を用意する（.env はコミットしない）
cp .env.example .env
python3 -c "import secrets; print('APP_SECRET_KEY=' + secrets.token_urlsafe(48))"

sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo $EDITOR /etc/caddy/Caddyfile     # FQDN・許可元IP・ハッシュを実際の値にする
sudo systemctl reload caddy

docker compose -f compose.yaml -f deploy/compose.public.yaml build
docker compose -f compose.yaml -f deploy/compose.public.yaml run --rm web python -m app.cli init-db
docker compose -f compose.yaml -f deploy/compose.public.yaml run --rm web \
  python -m app.cli create-operator teacher1 --display-name "講師1"
docker compose -f compose.yaml -f deploy/compose.public.yaml up -d
```

### 5. 配置後の確認

- `https://<FQDN>/` に、前段の認証を経てログイン画面が出る
- 前段の認証を通らないと何も見えない
- `http://<FQDN>:8000/` には**つながらない**
- ログイン後、Cookie に `Secure` が付いている（ブラウザの開発者ツールで確認）
- 実機（iPhone Safari / Android Chrome）で3D表示と操作ができる
  → 確認項目は `docs/quality-test-plan.md`

## 実APIを有効にする場合

限定公開環境に置いただけでは実API生成は行われません。
`docs/provider-contracts.md` の第3節と README の「実APIの有効化について」を先に済ませてください。

APIキーは `.env` に置き、**worker のみ**に渡ります（外部送信を行うのは worker のため）。

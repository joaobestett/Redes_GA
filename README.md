# P2P File Sync over UDP — README

> Trabalho Prático — **Redes de Computadores I** (UDP)  
> Implementação em **Python 3.9+**, sem bibliotecas de terceiros.  
>
> Este README explica **como executar e testar em dois ambientes** (Windows e Linux)

---

## 1) Visão geral do sistema

Cada **nó (peer)** executa simultaneamente as funções de **servidor** (recebe e grava arquivos) e **cliente** (solicita e envia arquivos) via **UDP**.  
- Topologia: **P2P estático** — a lista de peers é informada na linha de comando.  
- Diretório sincronizado: cada nó aponta para uma pasta local (`--dir`) que será mantida em sincronia.  
- **Adição/remoção**: novos arquivos adicionados à pasta local são anunciados, e remoções são propagadas.  
- **Transferência**: protocolo simples **stop‑and‑wait** com ACK por pacote (`DATA|...` + `ACK|...`).  
- **Listagem**: nós anunciam periodicamente `LIST` com `(nome, tamanho, sha256)` dos arquivos.  
- **Sumário**: logs legíveis com estado local e peers (critério “apresentação das informações sumarizadas”).


---

## 2) Pré‑requisitos

- **Python 3.9+** (Windows, Linux, macOS).  
- Porta UDP **livre** por nó (ex.: `9001`, `9002`, `9003`).  
- Permitir tráfego UDP no firewall local (ver seção *Solução de problemas*).

> **Sem dependências externas.**

---

## 3) Execução rápida (três nós) — *Windows* e *Linux*

Crie três pastas: `tmpA`, `tmpB`, `tmpC`. Em três terminais separados:

### Nó A
```bash
python peer.py --me 127.0.0.1:9001 --peers 127.0.0.1:9002,127.0.0.1:9003 --dir tmpA
```

### Nó B
```bash
python peer.py --me 127.0.0.1:9002 --peers 127.0.0.1:9001,127.0.0.1:9003 --dir tmpB
```

### Nó C
```bash
python peer.py --me 127.0.0.1:9003 --peers 127.0.0.1:9001,127.0.0.1:9002 --dir tmpC
```

> Se preferir **endereços reais** (máquinas diferentes), substitua `127.0.0.1` pelos IPs das máquinas. Certifique‑se de que elas se enxergam na rede e que as portas UDP estão liberadas.

---

## 4) Plano de teste mínimo 

### Ambiente 1 — *Windows * (PowerShell)
1. **Start** os três nós (A, B, C) conforme acima.
2. Em `tmpA`, crie um arquivo:
   ```powershell
   echo "hello from A" > tmpA\hello.txt
   ```
3. Observe nos logs: os nós anunciam `LIST` e B/C solicitam `GET`. Em poucos instantes, `hello.txt` deve aparecer em `tmpB` e `tmpC`.
4. **Valide a integridade**: o log reporta `Received 'hello.txt' [...]` e o sumário lista o SHA256.
5. **Remoção**: delete em A:
   ```powershell
   del tmpA\hello.txt
   ```
   Verifique que B e C reportam remoção: `[DEL] Removed 'hello.txt' due to peer deletion`.
6. **Novo peer**: abra um 4º terminal e rode:
   ```powershell
   python peer.py --me 127.0.0.1:9004 --peers 127.0.0.1:9001,127.0.0.1:9002,127.0.0.1:9003 --dir tmpD
   ```
   Crie um arquivo em qualquer pasta (ex.: `tmpB\win.txt`) e confirme que chega no novo peer `tmpD`.

### Ambiente 2 — **Linux** (Ubuntu/Debian/Fedora)
1. Instale Python 3.9+ (se necessário) e crie as pastas `tmpA`, `tmpB`, `tmpC`.
2. Em três shells:
   ```bash
   python3 peer.py --me 0.0.0.0:9001 --peers 192.168.0.20:9002,192.168.0.30:9003 --dir tmpA
   python3 peer.py --me 0.0.0.0:9002 --peers 192.168.0.10:9001,192.168.0.30:9003 --dir tmpB
   python3 peer.py --me 0.0.0.0:9003 --peers 192.168.0.10:9001,192.168.0.20:9002 --dir tmpC
   ```
   > Ajuste os IPs para as máquinas reais. `0.0.0.0` escuta em todas as interfaces.
3. Em `tmpC`, crie:
   ```bash
   echo "hello from C" > tmpC/hello_linux.txt
   ```
4. Confirme replicação para `tmpA` e `tmpB` e depois **remova** o arquivo em `tmpC`. Verifique propagação da remoção.
5. **Novo peer**: inicie `peer.py` como `--me 0.0.0.0:9004 --peers ... --dir tmpD` e valide que recebe arquivos existentes.

---

## 5) O que verificar na apresentação (checklist de avaliação)

1. **Organização da apresentação oral (1)**: explique a arquitetura, mensagens (`LIST`, `GET`, `DEL`, `DATA`, `ACK`), *stop‑and‑wait* e decisões de engenharia.
2. **Servidor (1)**: recepção de `DATA`, armazenamento, ACK, verificação de SHA256.
3. **Cliente (1)**: envio de `LIST`, `GET`, `DEL`, transmissão segmentada com *retries* e timeouts.
4. **Integração C/S (1)**: ambos no mesmo processo via *threads*.
5. **Adição (1)**: `scanner` detecta novos arquivos e `announcer` divulga via `LIST`.
6. **Remoção (1)**: `scanner` emite `DEL` e peers executam exclusão.
7. **Lista de arquivos (1)**: mensagem `LIST|N|name:size:sha;...` e logs com sumário.
8. **Sumário (1)**: `_log_summary` imprime estado local + peers após eventos.
9. **Testes em 2 ambientes (1)**: mostrar execução em **Windows** e **Linux** (prints/printscreens ou vídeo curto).
10. **Adição de novo peer (1)**: subir `--me :9004` com `--peers` apontando para os demais e demonstrar sincronização completa.

---

## 6) Mensagens do protocolo

- `LIST|<n>|name:size:sha;...` — anúncio periódico do índice local.  
- `GET|name|sha|size` — solicita arquivo com hash/tamanho esperado.  
- `DEL|name|sha` — avisa remoção (usa último hash conhecido quando houver).  
- `DATA|name|sha|seq|total\n<bytes>` — pacote de dados UDP; `seq ∈ [0,total)`  
- `ACK|name|seq` — confirmação por *chunk* (stop‑and‑wait).

**Confiabilidade**: *timeout* + `MAX_RETRIES` por chunk. Em caso de falha, a transferência é abortada e tentada novamente na próxima rodada de `LIST/GET`.

---

## 7) Como **adicionar um novo peer** à rede em tempo de execução

1. Inicie o novo peer com todos os outros em `--peers`. Exemplo (Windows):
   ```powershell
   python peer.py --me 127.0.0.1:9004 --peers 127.0.0.1:9001,127.0.0.1:9002,127.0.0.1:9003 --dir tmpD
   ```
2. O novo peer ouvirá anúncios `LIST` dos demais e solicitará qualquer arquivo faltante.  
3. Faça um *smoke test*: crie um arquivo em `tmpA` e confirme que chega em `tmpD`.

---

## 8) Boas práticas para demonstração

- Mostre os **logs de sumário** ao iniciar e após eventos de adicionar/remover.  
- Tenha um conjunto de **arquivos exemplo** (≥ 2 arquivos pequenos e 1 arquivo > 1MB).  
- Demonstre um **envio interrompido** (desligue um peer, religue e veja a reconciliação via `LIST`).  
- Explique por que foi escolhido **UDP** (simples, controle da confiabilidade na aplicação, alinhado ao enunciado).

---

## 9) Solução de problemas

### Windows (PowerShell / CMD)
- **Firewall**: permitir **UDP** nas portas usadas (e aplicativo `python.exe`), ou desativar temporariamente para teste.
- **ConnectionResetError (WSAECONNRESET)** ao `recvfrom`: o código aplica um *workaround* com `ioctl(0x9800000C)` quando disponível.
- **Porta em uso**: troque `--me :PORTA` ou finalize processos anteriores.

### Linux
- **Firewalld/ufw**: liberar portas UDP (`sudo ufw allow 9001/udp`, etc.).  
- **Permissões**: garanta escrita nas pastas `tmpX`.

### Geral
- **Nada replica**: confira se todos os peers constam em `--peers` de todos os nós, e se IPs/portas estão corretos.  
- **Arquivo não aparece**: aguarde o `ANNOUNCE_INTERVAL` (padrão 5s) e confira `SCAN_INTERVAL` (2s).  
- **SHA256 mismatch**: pode indicar truncamento por perda de pacotes acima do tolerável; teste numa rede local estável ou reduza `CHUNK_SIZE`.

---

## 10) Parâmetros e *tuning*

- `CHUNK_SIZE` (default 8192): tamanho do payload por **DATA**.  
- `ACK_TIMEOUT` (0.8s): tempo de espera por **ACK**.  
- `MAX_RETRIES` (8): tentativas por chunk antes de abortar.  
- `SCAN_INTERVAL` (2s): frequência de varredura de diretório.  
- `ANNOUNCE_INTERVAL` (5s): frequência de envio de `LIST`.  
- `SOCKET_TIMEOUT` (0.2s): *poll* do `recv` para *shutdown* limpo.

---

## 11) Scripts úteis (opcional)

### Windows — start 3 peers (PowerShell)
```powershell
mkdir tmpA,tmpB,tmpC -ErrorAction SilentlyContinue
Start-Process -NoNewWindow powershell -ArgumentList 'python peer.py --me 127.0.0.1:9001 --peers 127.0.0.1:9002,127.0.0.1:9003 --dir tmpA'
Start-Process -NoNewWindow powershell -ArgumentList 'python peer.py --me 127.0.0.1:9002 --peers 127.0.0.1:9001,127.0.0.1:9003 --dir tmpB'
Start-Process -NoNewWindow powershell -ArgumentList 'python peer.py --me 127.0.0.1:9003 --peers 127.0.0.1:9001,127.0.0.1:9002 --dir tmpC'
```

### Linux — start 3 peers (bash)
```bash
mkdir -p tmpA tmpB tmpC
python3 peer.py --me 0.0.0.0:9001 --peers 127.0.0.1:9002,127.0.0.1:9003 --dir tmpA &
python3 peer.py --me 0.0.0.0:9002 --peers 127.0.0.1:9001,127.0.0.1:9003 --dir tmpB &
python3 peer.py --me 0.0.0.0:9003 --peers 127.0.0.1:9001,127.0.0.1:9002 --dir tmpC &
wait
```

---

## 12) Estrutura do repositório sugerida

```
.
├── peer.py          # código principal (nó P2P, servidor+cliente)
├── README.md        # este arquivo
├── tmpA/ tmpB/ tmpC # pastas de exemplo (ignoráveis no Git)
└── .gitignore       # ignore tmp*
```

## 13) Encerramento

Para finalizar um nó, use `Ctrl+C`. O *shutdown* é limpo e fecha o socket.

Qualquer dúvida, rode com três terminais lado a lado e acompanhe os logs — eles foram pensados para **explicar o que está acontecendo** em termos de protocolo e fluxo.

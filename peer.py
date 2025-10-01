import argparse # para linha de comando
import os # para operações do sistema   
import socket # para comunicação UDP
import threading # para threads
import time # para temporizações 
import pathlib # para manipulação de caminhos
import hashlib # para SHA-256

# ====== Constantes de   protocolo ======
CHUNK_SIZE = 8192          # tamanho de cada pedaço (chunk) ao enviar arquivo
ACK_TIMEOUT = 0.8          # tempo para esperar um ACK de chunk (segundos)
MAX_RETRIES = 6            # quantas tentativas de reenvio por chunk
SCAN_INTERVAL = 2.0        # de quanto em quanto tempo escaneio o diretório local
ANNOUNCE_INTERVAL = 5.0    # intervalo para anunciar meu inventário (LIST) aos peers
SOCKET_TIMEOUT = 0.2       # timeout do socket pra não travar as threads

# ====== Helpers de protocolo/arquivo ======
def parse_addr(s):
    """ Converte 'host:port' para tupla (host, int(port)). """
    host, port = s.split(':')
    return host.strip(), int(port)

def sha256_file(path: pathlib.Path) -> str:
    """ Calcula SHA-256 do arquivo (para validar integridade no destino). """
    h = hashlib.sha256()
    with path.open('rb') as f:
        while True:
            b = f.read(1024 * 1024)  # leio em blocos grandes para não estourar memória
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def scan_dir(root: pathlib.Path):
    """ Gera um índice local: {nome: (tamanho, sha)} só com arquivos da raiz. """
    idx = {}
    for p in root.glob('*'):
        if p.is_file():
            try:
                st = p.stat()
                idx[p.name] = (st.st_size, sha256_file(p))
            except Exception:
                # se der erro em um arquivo (em uso, etc), ignoro pra não travar
                pass
    return idx  # {name: (size, sha)}

# Serializadores das mensagens de controle (texto)
def encode_list(files_dict):
    """ LIST|n|name:size:sha;...  -> anuncio meu inventário """
    parts = [f"{n}:{sz}:{sha}" for n,(sz,sha) in files_dict.items()]
    return f"LIST|{len(parts)}|" + ";".join(parts)

def encode_get(name, sha, size):
    """ GET|name|sha|size  -> peço um arquivo (especificando versão) """
    return f"GET|{name}|{sha}|{size}"

def encode_del(name, sha):
    """ DEL|name|sha  -> aviso que deletei esse arquivo (local e remoto) """
    return f"DEL|{name}|{sha}"

def encode_ack(name, seq):
    """ ACK|name|seq  -> confirmo que recebi o chunk 'seq' desse arquivo """
    return f"ACK|{name}|{seq}"

# ====== Classe principal do nó P2P ======
class Node:
    def __init__(self, me, peers, root: pathlib.Path):
        self.me = me
        # Endereço deste nó e lista de peers 
        self.peers = [p for p in peers if p != me]

        # Diretório que vou sincronizar
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

        # Socket UDP: bind no meu endereço e timeout curto pra não bloquear loop
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(self.me)
        self.sock.settimeout(SOCKET_TIMEOUT)

        
        try:
            if os.name == 'nt':
                self.sock.ioctl(0x9800000C, b'\x00\x00\x00\x00')
        except Exception:
            pass  # se der erro, ignoro

        # Índice local (nome -> (tamanho, sha))
        self.local_index = scan_dir(self.root)

        # Buffers de recepção por arquivo:
        # - rx_buf[name][seq] = dados do chunk
        # - rx_total[name] = quantos chunks espero ao todo
        # - rx_sha[name] = sha final esperado do arquivo
        self.rx_buf = {}
        self.rx_total = {}
        self.rx_sha = {}

        # Controle das threads
        self.stop_event = threading.Event()
        self.threads = []

    def start(self):
        """ Sobe as threads: recepção, anúncio e scanner de diretório. """
        self.threads = [
            threading.Thread(target=self._recv_loop, daemon=True),
            threading.Thread(target=self._announcer, daemon=True),
            threading.Thread(target=self._scanner, daemon=True),
        ]
        for t in self.threads:
            t.start()
        self._log("Started")

    def stop(self):
        """ Pede pras threads pararem e fecha o socket. """
        self.stop_event.set()
        for t in self.threads:
            t.join(timeout=1.0)
        self.sock.close()

    # ----------------- Rede: recepção -----------------
    def _recv_loop(self):
        """ Fica ouvindo UDP: se for DATA, monta arquivo; senão, trata controle. """
        while not self.stop_event.is_set():
            try:
                try:
                    data, addr = self.sock.recvfrom(65535)  # recebo pacote
                except ConnectionResetError:
                    continue  
            except socket.timeout:
                continue  # sem dados, loop continua 

            if data.startswith(b'DATA|'):
                # DATA|name|sha|seq|total\n<bytes>
                try:
                    header, payload = data.split(b'\n', 1)
                    _, name, sha, seq_s, total_s = header.decode('utf-8', 'ignore').split('|', 4)
                    seq = int(seq_s); total = int(total_s)
                except Exception:
                    continue  # se o cabeçalho vier zoado, ignoro

                # Mando ACK imediato pro remetente (stop-and-wait)
                self.sock.sendto(encode_ack(name, seq).encode('utf-8'), addr)

                # Guardo o chunk na posição correta do arquivo
                buf = self.rx_buf.setdefault(name, {})
                if seq not in buf:               # evita duplicar chunk em caso de retransmissão
                    buf[seq] = payload
                self.rx_total[name] = total
                self.rx_sha[name] = sha

                # Se já recebi todos os chunks desse arquivo, reconstruo
                if len(buf) == total:
                    data = b''.join(buf[i] for i in range(total))
                    # Confiro integridade com SHA-256 do arquivo inteiro
                    if hashlib.sha256(data).hexdigest() == self.rx_sha[name]:
                        (self.root / name).write_bytes(data)
                        sz = (self.root / name).stat().st_size
                        self.local_index[name] = (sz, self.rx_sha[name])
                        self._log(f"OK receive '{name}' ({sz} bytes)")
                    else:
                        self._log(f"ERR sha mismatch '{name}' -> discarded")
                    # Limpo buffers desse arquivo 
                    self.rx_buf.pop(name, None)
                    self.rx_total.pop(name, None)
                    self.rx_sha.pop(name, None)
            else:
                # Mensagem de controle em texto (LIST/GET/DEL/ACK)
                msg = data.decode('utf-8', 'ignore')
                self._handle_control(addr, msg)

    def _handle_control(self, addr, msg: str):
        """ Decide ações para LIST/GET/DEL/ACK. """
        if msg.startswith('LIST|'):
            # LIST|n|name:size:sha;...
            try:
                _, n_str, body = msg.split('|', 2)
                entries = body.split(';') if body else []
                for ent in entries:
                    if not ent:
                        continue
                    name, size_s, sha = ent.split(':', 2)
                    size = int(size_s)
                    have = self.local_index.get(name)
                    #  difere (tamanho/hash), peço com GET
                    if (have is None) or (have[0] != size) or (have[1] != sha):
                        self.sock.sendto(encode_get(name, sha, size).encode('utf-8'), addr)
            except Exception:
                pass  # ignoro LIST 

        elif msg.startswith('GET|'):
            # GET|name|sha|size  
            try:
                _, name, sha, size_s = msg.split('|', 3)
                have = self.local_index.get(name)
                if not have:
                    return  
                if have[1] != sha:
                    return  
                # Envio em chunks com confiabilidade (DATA/ACK)
                self._send_file(addr, name, have[1], have[0])
            except Exception:
                pass

        elif msg.startswith('DEL|'):
            # DEL|name|sha  
            try:
                _, name, sha = msg.split('|', 2)
                have = self.local_index.get(name)
                if have and (have[1] == sha):
                    try:
                        (self.root / name).unlink(missing_ok=True)
                        self.local_index.pop(name, None)
                        self._log(f"DEL from peer -> removed '{name}'")
                    except Exception:
                        pass
            except Exception:
                pass

        elif msg.startswith('ACK|'):
            # ACK de chunk é tratado lá dentro do _send_file 
            pass

    # ----------------- Rede: envio (DATA/ACK) -----------------
    def _send_file(self, addr, name, sha, size):
        """ Envia o arquivo em chunks (stop-and-wait): DATA -> espera ACK -> próximo. """
        path = self.root / name
        data = path.read_bytes()
        total = (size + CHUNK_SIZE - 1) // CHUNK_SIZE  # arredonda pra cima

        for seq in range(total):
            start = seq * CHUNK_SIZE
            chunk = data[start:start+CHUNK_SIZE]
            # Cabeçalho DATA + quebra de linha, depois os bytes do chunk
            header = f"DATA|{name}|{sha}|{seq}|{total}\n".encode('utf-8')
            pkt = header + chunk

            retries = 0
            while retries <= MAX_RETRIES:
                try:
                    self.sock.sendto(pkt, addr)          # envio o chunk
                    self.sock.settimeout(ACK_TIMEOUT)     # espero ACK desse seq
                    a, _ = self.sock.recvfrom(2048)
                    if a.startswith(b'ACK|'):
                        try:
                            _, ack_name, ack_seq_s = a.decode('utf-8', 'ignore').split('|', 2)
                            if ack_name == name and int(ack_seq_s) == seq:
                                break  # ACK correto recebido, bora pro próximo chunk
                        except Exception:
                            pass
                    retries += 1  # veio algo estranho, conto tentativa e reenvio
                except socket.timeout:
                    retries += 1  # não chegou ACK a tempo, reenvio

            if retries > MAX_RETRIES:
                # Desisto desse arquivo por enquanto (peer pode estar lento/offline)
                self._log(f"ERR send chunk {seq}/{total} '{name}' -> {addr}")
                return

        self._log(f"OK sent '{name}' to {addr} ({size} bytes)")

    # ------------- Tarefas periódicas  -------------
    def _announcer(self):
        """ De tempos em tempos, mando meu LIST pra todos, forçando reconciliação. """
        last = 0.0
        while not self.stop_event.is_set():
            now = time.time()
            if now - last >= ANNOUNCE_INTERVAL:
                msg = encode_list(self.local_index).encode('utf-8')
                for p in self.peers:
                    self.sock.sendto(msg, p)
                last = now
            time.sleep(0.2)  # pequeno sleep pra não consumir CPU à toa

    def _scanner(self):
        """ Observa mudanças locais (add/remove) e propaga pros peers. """
        prev = set(self.local_index.keys())
        while not self.stop_event.is_set():
            time.sleep(SCAN_INTERVAL)
            cur_idx = scan_dir(self.root)
            cur = set(cur_idx.keys())

            # adições locais -> atualizo índice e aviso (LIST) pra geral pedir com GET
            for name in (cur - prev):
                self.local_index[name] = cur_idx[name]
                self._log(f"ADD local '{name}'")
                self._broadcast_list()

            # remoções locais -> mando DEL pra todo mundo remover também (mesma versão)
            for name in (prev - cur):
                sha = self.local_index.get(name, (0, ""))[1]
                msg = encode_del(name, sha).encode('utf-8')
                for p in self.peers:
                    self.sock.sendto(msg, p)
                self.local_index.pop(name, None)
                self._log(f"DEL local '{name}' + propagated")

            prev = set(self.local_index.keys())

    def _broadcast_list(self):
        """ Manda um LIST imediato (uso quando detecto add local). """
        msg = encode_list(self.local_index).encode('utf-8')
        for p in self.peers:
            self.sock.sendto(msg, p)

    # ---------------- log  ----------------
    def _log(self, title):
        """ Mostra meu estado atual (útil pra apresentar no laboratório). """
        print("----------------------------------------------------")
        print(f"{title}")
        print(f"Me: {self.me} | Dir: {self.root}")
        if self.local_index:
            for n,(sz,sha) in self.local_index.items():
                print(f"  {n}  {sz}B  sha={sha[:10]}...")
        else:
            print("  (sem arquivos)")
        print("Peers:", ", ".join(f"{h}:{p}" for h,p in self.peers) or "(none)")
        print("----------------------------------------------------")

# ====== Entrada do programa ======
def main():
    # configurar endereço do nó, peers e diretório a sincronizar
    ap = argparse.ArgumentParser(description="P2P UDP File Sync (mínimo)")
    ap.add_argument("--me", required=True, help="host:port deste nó")
    ap.add_argument("--peers", default="", help="lista de peers host:port separados por vírgula")
    ap.add_argument("--dir", default="tmp", help="diretório de dados")
    args = ap.parse_args()

    me = parse_addr(args.me)
    peers = [parse_addr(s) for s in args.peers.split(',') if s.strip()]
    root = pathlib.Path(args.dir).resolve()

    node = Node(me, peers, root)
    try:
        node.start()         # sobe as três threads e começa a sincronização
        while True:
            time.sleep(1.0)  # mantém o processo vivo 
        print("\n[INFO] Encerrando...")
    finally:
        node.stop()       

if __name__ == "__main__":
    main()
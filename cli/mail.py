"""Local read-only Gmail connection. No model, sends or mailbox modifications."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
os.environ.setdefault('LANGHIRE_DATA_DIR',str(ROOT/'.local'))
from backend.core.config import get_data_dir
from backend.core.gmail_mailbox import GmailMailbox,MailError


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('status');sub.add_parser('check');sub.add_parser('setup')
    connect=sub.add_parser('connect');connect.add_argument('--client',type=Path)
    args=parser.parse_args();mail=GmailMailbox(get_data_dir())
    try:
        if args.command=='setup':
            print('1. Ative a Gmail API em um projeto seu: https://console.cloud.google.com/apis/library/gmail.googleapis.com')
            print('2. Configure a tela de consentimento para sua conta e crie um cliente OAuth do tipo App para computador: https://console.cloud.google.com/auth/clients')
            print('3. Salve o JSON baixado em:',mail.directory/'google-client.json')
            print('4. Execute: .venv\\Scripts\\python.exe -m cli.mail connect')
            print('Permissao solicitada: somente leitura. Nao use uma senha do Gmail.');return
        if args.command=='connect':print(json.dumps(mail.connect(args.client),ensure_ascii=False,indent=2))
        else:
            if args.command=='check':mail.open()
            print(json.dumps(dict(mail.status(),verified_online=args.command=='check'),ensure_ascii=False,indent=2))
    except MailError as error:parser.exit(1,str(error)+'\n')
    finally:mail.close()


if __name__=='__main__':main()

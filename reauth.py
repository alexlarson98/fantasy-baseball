"""
Re-authorize Yahoo with WRITE permission.

Your current token can only read. Yahoo grants write access at the *app*
level, not the token level, so refreshing won't help -- you have to flip the
app's permission and then mint a new token.

  1. Go to https://developer.yahoo.com/apps/ and open your app.
  2. Under API Permissions -> Fantasy Sports, tick **Read/Write** (not Read).
  3. Save, then run this script and follow the browser prompt.

Your old token is backed up to oauth_token.json.bak first.
"""
import json
import os
import shutil

from dotenv import load_dotenv
from yahoo_oauth import OAuth2

load_dotenv()

TOKEN_PATH = 'oauth_token.json'


def main():
    client_id = os.getenv('YAHOO_CLIENT_ID')
    client_secret = os.getenv('YAHOO_CLIENT_SECRET')
    if not client_id or not client_secret:
        raise SystemExit('YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET missing from .env')

    if os.path.exists(TOKEN_PATH):
        shutil.copy(TOKEN_PATH, TOKEN_PATH + '.bak')
        print(f'Backed up existing token to {TOKEN_PATH}.bak')

    # A file with credentials but no access_token makes yahoo_oauth run the full
    # browser consent flow, which is what re-grants us the new permission.
    with open(TOKEN_PATH, 'w', encoding='utf-8') as f:
        json.dump({'consumer_key': client_id, 'consumer_secret': client_secret}, f, indent=2)

    OAuth2(None, None, from_file=TOKEN_PATH)
    print('\nDone. New token written to oauth_token.json.')
    print('Verify write access with:  python -c "from src.yahoo_client import YahooClient;'
          ' print(YahooClient().get_team_key(__import__(\'os\').getenv(\'YAHOO_LEAGUE_ID\')))"')


if __name__ == '__main__':
    main()

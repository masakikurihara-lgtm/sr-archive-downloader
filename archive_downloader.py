import streamlit as st
import requests
from bs4 import BeautifulSoup
import time
import re
import datetime
import pandas as pd
import io

# ==============================================================================
# ----------------- 設定 -----------------
# ==============================================================================

try:
    # 既存のオーガナイザーCookieを使用
    AUTH_COOKIE_STRING = st.secrets["showroom"]["auth_cookie_string"]
except KeyError:
    st.error("🚨 認証設定がされていません。")
    st.stop()

BASE_URL = "https://www.showroom-live.com"
# ルームリストのCSV URL
ROOM_LIST_URL = "https://mksoul-pro.com/showroom/file/room_list.csv"

# JSTタイムゾーン定義
JST = datetime.timezone(datetime.timedelta(hours=9), 'JST') 

# ==============================================================================
# ----------------- CSVデータ処理関数 (修正: ID/URLマッピング変更) -----------------
# ==============================================================================

@st.cache_data(ttl=3600) # 1時間キャッシュ
def load_room_data(room_list_url):
    """
    CSVからルームデータ（アカウントID -> ルームID）のマッピングを読み込む。
    CSV構造: [1列目(A): ルームID] ... [4列目(D): アカウントID] ...
    """
    try:
        st.info("ルームリストCSVをダウンロード中...")
        response = requests.get(room_list_url)
        response.raise_for_status()
        
        # Shift-JISまたはUTF-8を想定してデコードを試みる
        try:
            csv_data = response.content.decode('utf-8')
        except UnicodeDecodeError:
            csv_data = response.content.decode('shift_jis')

        csv_file = io.StringIO(csv_data)
        
        # 🚨 修正点①: ルームID(A列=インデックス0)を強制的に文字列として読み込む
        # C列は使わないため、A列とD列のみを対象とする
        df = pd.read_csv(csv_file, dtype={0: str, 3: str})
        
        # ユーザー指定の列インデックス（0始まりで A=0, D=3）
        if df.shape[1] < 4:
            st.error(f"🚨 CSVの列数が不足しています。現在の列数: {df.shape[1]}。A列(ルームID)とD列(アカウントID)が必要です。")
            return None
        
        account_id_col_name = df.columns[3]
        room_id_col_name = df.columns[0]
        
        # 🚨 修正点②: 辞書を構築: {アカウントID: ルームID}
        # 値が数値になっていないか念のためastype(str)を適用
        room_map = df.set_index(account_id_col_name)[room_id_col_name].dropna().astype(str).to_dict()
        
        return room_map
    
    except requests.exceptions.RequestException as e:
        st.error(f"🚨 ルームリストのダウンロードに失敗しました: {e}")
        return None
    except Exception as e:
        st.error(f"🚨 ルームリストの解析に失敗しました。CSVの構造を確認してください: {e}")
        st.exception(e)
        return None

# ==============================================================================
# ----------------- APIアクセス関数 (新規追加) -----------------
# ==============================================================================

@st.cache_data(ttl=600) # 10分キャッシュ
def get_room_url_key(room_id):
    """ルームIDからSHOWROOMのAPIを叩いてroom_url_key（ルームURL）を取得する"""
    PROFILE_API_URL = f"{BASE_URL}/api/room/profile?room_id={room_id}"
    #st.info(f"ルームID `{room_id}` に基づき、APIから正確なルームURLキーを取得中...")
    st.info(f"ルームURLキーを取得中...")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
    }

    try:
        r = requests.get(PROFILE_API_URL, headers=headers, timeout=5)
        r.raise_for_status()
        
        data = r.json()
        room_url_key = data.get("room_url_key")
        
        if room_url_key:
            return room_url_key
        else:
            #st.error(f"🚨 API応答に `room_url_key` が含まれていません。ルームID `{room_id}` が不正な可能性があります。")
            st.error(f"🚨 `room_url_key` が見つかりません。ルームID `{room_id}` が不正な可能性があります。")
            return None
            
    except requests.exceptions.RequestException as e:
        #st.error(f"🚨 ルームプロフィールAPIへのアクセスに失敗しました。ルームIDが不正、またはネットワークエラーです: {e}")
        st.error(f"🚨 プロフィール情報へのアクセスに失敗しました。ルームIDが不正、またはネットワークエラーです: {e}")
        return None
    except Exception as e:
        st.error(f"🚨 プロフィール情報の応答の解析に失敗しました: {e}")
        return None

# ==============================================================================
# ----------------- セッション構築関数 -----------------
# ... (変更なし) ...
# ==============================================================================

def create_authenticated_session(cookie_string):
    """手動で取得したCookie文字列から認証済みRequestsセッションを構築する"""
    st.info("認証セッションを構築します...")
    session = requests.Session()
    try:
        cookies_dict = {}
        for item in cookie_string.split(';'):
            item = item.strip()
            if '=' in item:
                name, value = item.split('=', 1)
                cookies_dict[name.strip()] = value.strip()
        cookies_dict['i18n_redirected'] = 'ja'
        session.cookies.update(cookies_dict)
        
        if not cookies_dict:
            st.error("🚨 有効な認証セッションをを解析できませんでした。")
            return None
             
        return session
    except Exception as e:
        st.error(f"認証セッションを解析中にエラーが発生しました: {e}")
        return None

# ==============================================================================
# ----------------- アーカイブスクレイピング関数 -----------------
# ... (変更なし) ...
# ==============================================================================

def scrape_live_archives(session, room_url_key):
    """アーカイブページにアクセスし、配信アーカイブデータとダウンロードリンクを抽出する"""
    ARCHIVE_URL = f"{BASE_URL}/room/{room_url_key}/live_archives"
    st.info(f"配信アーカイブページにアクセス中...")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
        'Referer': f"{BASE_URL}/room/{room_url_key}",
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
    }
    
    try:
        r = session.get(ARCHIVE_URL, headers=headers)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        st.error(f"🚨 アーカイブページへのアクセスに失敗しました。認証切れ、またはルームURLが不正な可能性があります: {e}")
        return None, None

    soup = BeautifulSoup(r.text, 'html.parser')
    archives = []
    
    # 1. ルーム名の抽出
    room_name_tag = soup.find('p', class_='head-main')
    room_name_full = room_name_tag.text.strip() if room_name_tag else "不明なルーム 配信アーカイブ一覧"
    # ルーム名から「 配信アーカイブ一覧」を除去
    room_name = room_name_full.replace(" 配信アーカイブ一覧", "").strip() if " 配信アーカイブ一覧" in room_name_full else room_name_full
    
    # 2. アーカイブデータの抽出
    table = soup.find('table', class_='table')
    
    if not table:
        if "ログイン" in r.text or "会員登録" in r.text or "サインイン" in r.text:
            st.error("🚨 認証切れです。管理者に照会してください。")
            return None, None
            
        st.warning("⚠️ 配信アーカイブが見つかりませんでした。データがまだ生成されていないか、ページ構造が変更された可能性があります。")
        return room_name, []

    tbody = table.find('tbody')
    if not tbody:
        return room_name, []
        
    rows = tbody.find_all('tr')
    
    for row in rows:
        td_tags = row.find_all('td')
        if len(td_tags) == 2:
            time_period = td_tags[0].text.strip()
            download_link_tag = td_tags[1].find('a', class_='btn-light-green')
            
            if download_link_tag and download_link_tag.get('href'):
                download_url = download_link_tag['href']
                # download属性の値があればファイル名として使用
                download_filename = download_link_tag.get('download', f"{room_url_key}_{time.time()}.mp4") 
                
                archives.append({
                    'time_period': time_period,
                    'download_url': download_url,
                    'download_filename': download_filename
                })
                
    return room_name, archives

# ==============================================================================
# ----------------- メイン関数 (修正: ルームIDからのURL取得を追加) -----------------
# ==============================================================================

def main():
    st.set_page_config(
        page_title="SHOWROOM 配信アーカイブDL",
        page_icon="💾",
    )
    st.markdown(
        "<h1 style='font-size:28px; text-align:center; color:#1f2937;'>💾 SHOWROOM 配信アーカイブ ダウンロードツール</h1>",
        unsafe_allow_html=True
    )
    st.markdown("<p style='text-align: center;'>⚠️ <b>注意</b>: このツールは、<b>管理者が認証セッションを許可している場合のみ</b>動作します。</p>", unsafe_allow_html=True)
    st.markdown("---")

    # 1. ルームリストの読み込み
    room_map = load_room_data(ROOM_LIST_URL)
    if room_map is None:
        return
    #st.success(f"✅ ルームリスト ({len(room_map)}件) の読み込みに成功しました。（アカウントID → ルームID）")
    st.success(f"✅ ルームリスト ({len(room_map)}件) の読み込みに成功しました。")

    # 2. アカウントIDの入力とルームIDの特定
    with st.form("archive_search_form"):
        st.markdown("##### 🔑 アカウントIDを入力してください")
        account_id_input = st.text_input(
            "アカウントID:", 
            placeholder="例: mksoul_live_001",
            type="password",
            key="account_id_input"
        )
        search_button = st.form_submit_button("アーカイブを表示")
        
    target_room_id = None
    
    if search_button:
        if not account_id_input:
            st.error("⚠️ アカウントIDを入力してください。")
            return
            
        if account_id_input in room_map:
            target_room_id = room_map[account_id_input]
            st.session_state['target_room_id'] = target_room_id
            st.session_state['account_id'] = account_id_input
            # st.rerun() は API呼び出し後に実行
        else:
            st.error(f"🚨 ルームリストにアカウントID `{account_id_input}` が見つかりません。")
            st.session_state['target_room_id'] = None
            return
    
    # フォーム外で再実行された場合の処理
    if 'target_room_id' not in st.session_state or not st.session_state['target_room_id']:
        st.warning("⚠️ アカウントIDを入力して「アーカイブを表示」ボタンを押してください。")
        return
    
    target_room_id = st.session_state['target_room_id']
    account_id_input = st.session_state['account_id']
    
    # 🚨 修正点③: ルームIDからroom_url_key（ルームURL）を取得
    room_url_key = get_room_url_key(target_room_id)
    if not room_url_key:
        return

    #st.markdown(f"**対象アカウント**: `{account_id_input}` / **ルームID**: `{target_room_id}`")
    st.success(f"✅ ルームURLキーを取得しました: `{room_url_key}`")
    st.info(f"現在の時刻（JST）: {datetime.datetime.now(JST).strftime('%Y/%m/%d %H:%M:%S')}")
    st.markdown("---")


    # 3. 認証セッションの構築
    session = create_authenticated_session(AUTH_COOKIE_STRING)
    if not session:
        return

    # 4. アーカイブデータのスクレイピング
    room_name, archives = scrape_live_archives(session, room_url_key)
    
    if room_name is None and archives is None: # 認証失敗
        return
    
    #st.header(f"ルーム名: {room_name} のアーカイブ")
    st.markdown(f"##### ルーム名: {room_name} のアーカイブ")
    
    if not archives:
        st.info("アーカイブは見つかりませんでした。（過去1ヶ月分のみ）")
        return

    # 5. 結果の表示
    st.markdown(f"**合計 {len(archives)} 件** のアーカイブが見つかりました。（過去1ヶ月分）")

    # ダウンロードリンクの表示
    for i, archive in enumerate(archives):
        time_span = archive['time_period']
        download_url = archive['download_url']
        filename = archive['download_filename']
        
        with st.container(border=True):
            st.markdown(f"**配信時間**: `{time_span}`")
            
            # ブラウザの「名前を付けてリンク先を保存」を促すためのHTMLボタン表示
            st.markdown(
                f'<a href="{download_url}" download="{filename}" class="stButton" target="_blank" style="text-decoration: none;">'
                f'<button style="background-color: #4CAF50; color: white; border: none; padding: 8px 16px; text-align: center; text-decoration: none; display: inline-block; font-size: 16px; margin: 4px 0px; cursor: pointer; border-radius: 4px; width: 100%;">'
                f'⬇️ {filename} をダウンロード'
                f'</button>'
                f'</a>',
                unsafe_allow_html=True
            )
            
if __name__ == "__main__":
    main()
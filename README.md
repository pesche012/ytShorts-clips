# YouTube Shorts 自動メーカー

YouTube URLを入力すると、次の処理を自動で行うWindows用アプリです。

1. YouTube動画を取得
2. YouTube字幕（手動字幕・自動字幕）を確認
3. 字幕があればタイムスタンプ付きで取得
4. 字幕がなければWhisperで文字起こし
5. OpenRouterで選択したLLMが見どころを7個選定
6. FFmpegで9:16の縦型動画を7本作成

## 最初の起動

1. `setup.bat` をダブルクリックします。
2. 初回だけPython、Deno、動画処理機能などを自動で準備します。数分かかる場合があります。
3. アプリが開いたら、YouTube URLとOpenRouter APIキーを入力します。
4. 「モデル一覧を更新」を押し、使いたいモデルを選びます。
5. 「7本のShortsを作成」を押します。

黒い画面が途中で閉じた場合は、同じフォルダーの `setup.log` に原因が保存されます。修正版ではエラー時に画面も閉じません。

2回目からは `start.bat` をダブルクリックするだけです。

OpenRouter APIキーは [OpenRouter Keys](https://openrouter.ai/settings/keys) で発行できます。モデルごとの利用料金はOpenRouter側で発生します。

## LLMモデルの選択

- `openrouter/auto`: 内容に合うモデルをOpenRouterが自動選択します。迷った場合はこれを使います。
- 「モデル一覧を更新」: 現在利用できるテキストモデルをOpenRouterから取得します。
- 一覧にないモデル: モデルID（例: `提供元/モデル名`）を入力欄へ直接入力できます。
- Claude、Gemini、GPT、DeepSeek、オープンモデルなど、OpenRouterで提供されるモデルを同じ画面から利用できます。
- JSON Schema非対応モデルを選んだ場合は、通常のJSON生成へ自動的に切り替わります。

## 出力先

完成した動画は、このフォルダー内の `outputs` に保存されます。各動画ごとに次の内容が入ります。

- `01_タイトル.mp4` 〜 `07_タイトル.mp4`
- `見どころ一覧.json`
- `文字起こし.txt`

## 設定の目安

- Whisper精度: 普段は「標準（small）」がおすすめです。字幕がある動画ではWhisperを使わないため、この設定は処理時間に影響しません。
- 動画サイズ: 最初は「720p（高速）」がおすすめです。
- ログイン動画: 年齢制限などで取得できない場合のみEdgeまたはChromeを選びます。対象動画へログイン済みのブラウザーを指定してください。

## 注意

- 自分が権利を持つ動画、またはダウンロード・編集・再投稿の許可を得た動画だけを使用してください。
- YouTubeやOpenRouter側の仕様変更により、依存機能の更新が必要になる場合があります。その場合は `setup.bat` をもう一度実行してください。
- OpenRouter APIキーを保存する場合、Windowsの `%APPDATA%\YouTubeShortMaker\settings.json` に保存されます。共有PCでは保存しないでください。

## 将来のLLM拡張

LLM接続は `llm_providers.py` に分離されています。動画取得・字幕・Whisper・切り抜き処理には手を入れず、`LLMProvider` を継承した接続クラスを追加して登録するだけで、別のLLMサービスを増やせる構成です。

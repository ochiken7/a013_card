"""Claude APIによるOCRテキストの構造化処理"""

import json
import re
import time
import logging

import anthropic
from flask import current_app

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたは日本の名刺情報を構造化するアシスタントです。
OCRで読み取った名刺のテキストを、指定されたJSON形式に変換してください。

ルール:
1. 読み取れた情報のみをセットする。推測で埋めない。
2. 該当する情報がない項目はnullにする。
3. 電話番号の種別は内容から判断する（TEL→main, 直通→direct, 携帯→mobile, FAX→fax）
4. メールアドレスが個人ドメインか会社ドメインかを判断する
5. 資格・肩書きは複数ある場合はすべて配列に入れる
6. フリガナやローマ字は記載がある場合のみセットする
7. 郵便番号は「〒」を除去し、数字とハイフンのみにする
8. 電話番号のフォーマットはそのまま維持する（ハイフン等）
9. 裏面テキストが提供された場合、事業内容・拠点情報をフリーテキストとして保存する"""

USER_PROMPT_TEMPLATE = """以下のOCRテキストから名刺情報を構造化してください。

【表面テキスト】
{front_text}

{back_section}

以下のJSON形式で出力してください。JSONのみを出力し、他の説明は不要です。

{{
  "company_name_ja": "会社名（日本語）",
  "company_name_en": "会社名（英語/ローマ字）※なければnull",
  "department": "部署名 ※なければnull",
  "position": "役職 ※なければnull",
  "name_kanji": "氏名（漢字）",
  "name_kana": "氏名（フリガナ）※なければnull",
  "name_romaji": "氏名（ローマ字）※なければnull",
  "phones": [
    {{
      "number": "電話番号",
      "type": "main|direct|mobile|fax"
    }}
  ],
  "emails": [
    {{
      "address": "メールアドレス",
      "type": "company|personal"
    }}
  ],
  "qualifications": ["資格・肩書き1", "資格・肩書き2"],
  "zip_code": "郵便番号（数字とハイフンのみ）※なければnull",
  "address": "住所（建物名除く）※なければnull",
  "building": "建物名・部屋番号 ※なければnull",
  "website": "WebサイトURL ※なければnull",
  "sns_info": "SNSアカウント情報 ※なければnull",
  "back_business_memo": "裏面の事業内容 ※なければnull",
  "back_branch_memo": "裏面の拠点情報 ※なければnull"
}}"""


def build_user_prompt(front_text, back_text=None):
    """ユーザープロンプトを生成"""
    if back_text:
        back_section = f"【裏面テキスト】\n{back_text}"
    else:
        back_section = "※裏面なし"
    return USER_PROMPT_TEMPLATE.format(front_text=front_text, back_section=back_section)


def extract_json_from_response(response_text):
    """レスポンスからJSONを抽出"""
    text = response_text.strip()
    # ```json ... ``` で囲まれている場合
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


def structure_card_data(front_text, back_text=None):
    """Claude APIで名刺テキストを構造化（リトライ付き）"""
    client = anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"])

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": build_user_prompt(front_text, back_text)}
                ],
            )
            response_text = message.content[0].text
            return extract_json_from_response(response_text)

        except json.JSONDecodeError:
            if attempt == max_retries:
                logger.error("Claude API: JSON解析に失敗しました")
                raise
            logger.warning(f"Claude API JSON parse retry {attempt + 1}")
            time.sleep(1)

        except anthropic.RateLimitError:
            if attempt == max_retries:
                raise
            logger.warning("Claude API rate limit, retrying...")
            time.sleep(2)

        except Exception as e:
            logger.error(f"Claude API error: {e}")
            raise


def structured_to_form_data(structured):
    """Claude APIの出力をフォーム表示用に変換（nullを空文字に）"""
    return {
        "company_name_ja": structured.get("company_name_ja") or "",
        "company_name_en": structured.get("company_name_en") or "",
        "department": structured.get("department") or "",
        "position": structured.get("position") or "",
        "name_kanji": structured.get("name_kanji") or "",
        "name_kana": structured.get("name_kana") or "",
        "name_romaji": structured.get("name_romaji") or "",
        "phones": structured.get("phones") or [],
        "emails": structured.get("emails") or [],
        "qualifications": structured.get("qualifications") or [],
        "zip_code": structured.get("zip_code") or "",
        "address": structured.get("address") or "",
        "building": structured.get("building") or "",
        "website": structured.get("website") or "",
        "sns_info": structured.get("sns_info") or "",
        "back_business_memo": structured.get("back_business_memo") or "",
        "back_branch_memo": structured.get("back_branch_memo") or "",
    }

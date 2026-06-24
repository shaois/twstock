class AIAnalyzer:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def analyze(self, stock_id: str, score_data: dict) -> dict:
        return {"stock_id": stock_id, "analysis": "AI 分析功能暫未啟用，請檢查 API Key。"}
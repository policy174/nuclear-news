"""발송 전 전문용어 관계 검사. 외부 AI 호출 없음."""
import copy
import re


def guard_storage_titles(result):
    result = copy.deepcopy(result)
    for block in result.get("blocks") or []:
        title = block.get("title", "")
        compact = re.sub(r"\s", "", title)
        body = re.sub(r"\s", "", " ".join(x.get("text", "") for x in block.get("lines") or []))
        # 건식 자체의 포화 논의는 가능하므로 '포화·운영 시점 비교'에 한정한다.
        ambiguous = ("건식" in compact and "포화" in compact and "습식" not in compact
                     and any(x in compact for x in ("운영", "운용", "가동")))
        if not ambiguous:
            continue
        if (re.search(r"습식.{0,20}포화", body)
                and re.search(r"건식.{0,20}(운영|운용|가동)", body)):
            block["title"] = "습식저장시설 포화 대비 건식저장시설 적기 가동과 일정 관리"
        else:
            raise ValueError("저장시설 제목의 포화 대상 불명확: 습식·건식 관계를 원문으로 확인해야 함")
    return result

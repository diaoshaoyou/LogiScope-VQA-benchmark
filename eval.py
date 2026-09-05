from collections import defaultdict
import os
import json
import re
import string
import math
import time
import requests

root = "/path/to/LogiScope-VQA/"


class APIPredictor():
    """
    LLM API caller for open-ended question judge evaluation.
    Users should adapt the predict method to their own API platform.
    """
    def __init__(self, api_key, base_url, model_name):
        self.api_key = api_key        # replace with your API key
        self.base_url = base_url      # replace with your API endpoint
        self.model_name = model_name  # replace with the model name to call

    def predict(self, messages):
        """
        Call the LLM API with an OpenAI-format messages list and return the response text.
        Below is an example (modify headers / data / response parsing for your own API):
        """
        headers = {
            'Authorization': f'Bearer {self.api_key}',  # adjust auth scheme per platform
            'Content-Type': 'application/json'
        }
        data = {
            "model": self.model_name,   # model service name
            "messages": messages,       # OpenAI-format messages
            "max_tokens": 4096,
            "stream": False,
        }
        try:
            response = requests.post(self.base_url, headers=headers, data=json.dumps(data))
            if response.status_code == 200:
                response_data = response.json()
                # parse text per your API response structure; OpenAI-compatible format below
                return response_data['choices'][0]['message']['content']
            else:
                print(response.text)
                return ""
        except Exception as e:
            print(f"API call error: {str(e)}")
            return ""

# open-ended evaluation model config (replace with your own API info)
API_KEY = "YOUR_API_KEY"       # replace with your API key
API_BASE_URL = "YOUR_BASE_URL"     # replace with your API endpoint
JUDGE_MODEL = "YOUR_MODEL_NAME"      # replace with the judge model name

JUDGE_SYSTEM_PROMPT = """你是一个严谨的简答题评分专家。你的任务是判断【学生回答】是否在语义上等价或覆盖了【标准答案】的核心要点。

# 评分规则
请基于以下逻辑进行判断：
1. **核心要点检查**：【学生回答】必须包含【标准答案】中所有的关键信息点。接受同义词替换、句式重组或合理的概括。
2. **事实一致性检查**：【学生回答】中不得包含与【标准答案】相矛盾的事实错误。如果存在任何事实性错误或逻辑矛盾，直接判定为 0。
3. **冗余信息处理**：如果【学生回答】包含了【标准答案】之外的额外正确信息，应予以忽略，不影响得分。
4. **缺失判定**：如果遗漏了任何一个关键要点，即使其他部分完全正确，也判定为 0。

# 输出格式
- 仅输出一个数字：`1` 表示正确（覆盖所有关键点且无错误），`0` 表示错误（有关键点缺失、事实错误或矛盾）。
- **严禁**输出任何解释、标点符号、换行符或其他文字。"""

JUDGE_USER_PROMPT = """【问题】
{question}

【标准答案】
{answer}

【学生回答】
{pred}"""


def extract_open_ended_answer(pred_text):
    """Extract the text after 'Final Answer:' or 'Final Answer：' in pred."""
    if not pred_text:
        return ""
    match = re.search(r"Final\s*Answer\s*[：:]\s*", pred_text)
    if match:
        return pred_text[match.end():].strip()
    return pred_text.strip()


def parse_open_ended_score(response_text):
    """Parse 0 or 1 from the judge model response."""
    text = response_text.strip()
    if text.startswith("1"):
        return 1
    if text.startswith("0"):
        return 0
    for ch in text:
        if ch in ("0", "1"):
            return int(ch)
    return 0


def judge_open_ended(item, predictor):
    """Score a single open-ended sample with LLM judge, returns score (0/1/-1)."""
    raw_pred = item.get("pred", "")
    if not raw_pred or not raw_pred.strip():
        return -1

    final_answer_pred = extract_open_ended_answer(raw_pred)
    user_prompt = JUDGE_USER_PROMPT.format(
        question=item.get("question", ""),
        answer=item.get("answer", ""),
        pred=final_answer_pred,
    )
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(3):
        try:
            response = predictor.predict(messages)
            if response and response.strip():
                return parse_open_ended_score(response)
        except Exception as e:
            print("  [WARN] index=%s attempt %d failed: %s" % (item.get("index"), attempt + 1, str(e)))
        if attempt < 2:
            time.sleep(1)
    return 0

def parse_option_pred(item, fail_num):
    """
    Parse the raw pred of an option question into a list of option letters.
    """
    if isinstance(item["pred"], str):
        if "```json" in item["pred"]:
            item["pred"] = item["pred"].split("```json")[-1].strip()
        if "```" in item["pred"]:
            item["pred"] = item["pred"].split("```")[0].strip()
        found_options = re.findall(r'[A-G]', item["pred"].upper())
        if found_options:
            # if option letters are found, store them directly as a list (e.g. "A" -> ["A"])
            item["pred"] = found_options
        else:
            # no simple letters found, fall back to json.loads
            try:
                # strip to remove residual brackets or whitespace
                cleaned_str = item["pred"].strip("[]'\" ")
                if cleaned_str in string.ascii_uppercase:
                    item["pred"] = [cleaned_str]
                else:
                    item["pred"] = json.loads(item["pred"])
                # ensure the result is a list
                if isinstance(item["pred"], str):
                    item["pred"] = [item["pred"]]
            except:
                item["pred"] = None
                return
    
    if isinstance(item["pred"], dict):
        if item["question_type"] == "option":
            # check common field names first
            final_pred = []
            for key in ['answer', '答案', 'result']:
                if key in item["pred"]:
                    value = item["pred"][key]
                    if isinstance(value, str):
                        final_pred.append(value.strip())
                    elif isinstance(value, list):
                        for tmp in value:
                            if isinstance(tmp, str):
                                final_pred.append(tmp.strip())
                    item["pred"] = final_pred
                    return
            # no common fields found, check if keys are option letters (A/B/C/D etc.)
            for key, value in item["pred"].items():
                if isinstance(key, str) and len(key) == 1 and key.upper() in string.ascii_uppercase:
                    final_pred.append(key.upper())
            item["pred"] = final_pred
            return

    elif isinstance(item["pred"], list):
        if len(item["pred"]) > 0 and isinstance(item["pred"][0], list):
            item["pred"] = item["pred"][0]
        # clean options: extract option letter only (e.g. "A. violation exists" -> "A")
        cleaned = []
        for elem in item["pred"]:
            if isinstance(elem, str):
                match = re.match(r'^([A-G])', elem.strip(), re.IGNORECASE)
                cleaned.append(match.group(1).upper() if match else elem)
            else:
                cleaned.append(elem)
        item["pred"] = cleaned
        return
    


def cal_option_acc(item_list):
    """
    Calculate accuracy for option questions.
    option: pred matches gt exactly -> 1 point
    """
    fail_num = 0
    cnt = 0
    L = len(item_list)
    for item in item_list:
        # empty pred counts as fail directly
        if item.get("pred") == "" or item.get("pred") == []:
            fail_num += 1
            continue
            
        parse_option_pred(item, fail_num)
        if item["pred"] == None:
            fail_num += 1
            continue
        
        if item["question_type"] == "option":
            try:
                if frozenset(item["answer"]) == frozenset(item["pred"]):
                    cnt += 1
            except:
                fail_num += 1
            

    acc = round(cnt/L, 3)
    return acc, L, fail_num


def cal_option_bias(item_list, epsilon=1e-8):
    """
    Calculate safety bias metric: bias = ln((FPR + ε) / (FNR + ε))
    Only for option questions. Option "A" means "violation/risk exists",
    option "B" means "no violation/no risk found".

    Interpretation:
    - bias > 0: tends toward "risky" (FPR higher than FNR)
    - bias < 0: tends toward "no risk" (FNR higher than FPR)
    - bias ≈ 0: no significant bias

    Returns: bias_score, total_valid
    """
    import math
    
    tp = 0  # gt risky, pred risky
    fp = 0  # gt no risk, pred risky
    tn = 0  # gt no risk, pred no risk
    fn = 0  # gt risky, pred no risk
    total_valid = 0
    
    for item in item_list:
        if item.get("question_type") != "option":
            continue
        
        answer = item.get("answer", [])
        pred = item.get("pred", [])
        
        if not answer or not pred or pred is None:
            continue

        # normalize answer and pred to sets
        try:
            answer_set = set(answer) if isinstance(answer, list) else {answer}
            pred_set = set(pred) if isinstance(pred, list) else {pred}
        except:
            continue
        
        # "A" means "violation/risk exists", "B" means "no violation/no risk"
        is_risk_gt = "A" in answer_set
        is_risk_pred = "A" in pred_set
        
        if is_risk_gt and is_risk_pred:
            tp += 1
        elif not is_risk_gt and is_risk_pred:
            fp += 1
        elif not is_risk_gt and not is_risk_pred:
            tn += 1
        elif is_risk_gt and not is_risk_pred:
            fn += 1
        
        total_valid += 1
    
    if total_valid == 0:
        return None, 0
    
    # compute FNR and FPR
    fnr = fn / (fn + tp) if (fn + tp) > 0 else 0.0
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    
    # bias = ln((FNR + ε) / (FPR + ε))
    # positive: FNR > FPR, optimistic bias (more misses); negative: FPR > FNR, conservative bias (more false alarms)
    raw_bias = math.log((fnr + epsilon) / (fpr + epsilon))
    
    # normalization: normalized_bias = tanh(bias / c), c=2
    c = 2.0
    bias_score = round(math.tanh(raw_bias / c), 4)
    
    return bias_score, total_valid


    

def binary_eval(test_path, predictor=None):
    """
    Evaluate a single model result file, supporting both option and open-ended questions.
    - option: exact option matching for acc
    - open-ended: LLM judge scoring (score field), empty pred gets -1
    Category info is read directly from l0/l1_category fields in the result file.
    """
    with open(test_path, 'r', encoding="utf-8") as f1:
        results = json.load(f1)

    # separate option and open-ended items
    option_items = [item for item in results if item.get("question_type") == "option"]
    open_ended_items = [item for item in results if item.get("question_type") == "open-ended"]

    # ===== open-ended evaluation: LLM judge scoring =====
    if open_ended_items and predictor is not None:
        print(f"  open-ended samples: {len(open_ended_items)}, starting LLM judge evaluation...")
        for idx, item in enumerate(open_ended_items):
            score = judge_open_ended(item, predictor)
            item["score"] = score
            print("  open-ended %d/%d (index=%s) score=%d" % (idx + 1, len(open_ended_items), item.get("index"), score))

    # ===== group by category dimensions (option + open-ended unified) =====
    angle_keys = ["all", "l0_category", "l1_category"]
    multi_angle_results = {k: defaultdict(list) for k in angle_keys}
    multi_angle_results["all"] = results  # "all" stores the full sample list directly

    for item in results:
        for angle in ["l0_category", "l1_category"]:
            val = item.get(angle, "")
            if val:
                multi_angle_results[angle][val].append(item)

    # ===== compute metrics =====
    evaluation = {k: defaultdict(lambda: {"total": 0, "fail": 0, "acc": 0}) for k in angle_keys}

    def eval_merged_items(item_list):
        """Compute merged acc (option + open-ended unified) for a group of samples, returns metrics dict."""
        option_list = [it for it in item_list if it.get("question_type") == "option"]
        open_ended_list = [it for it in item_list if it.get("question_type") == "open-ended"]

        # option: correct count, total, parse failures
        _, option_total, option_fail = cal_option_acc(option_list) if option_list else (0, 0, 0)
        option_correct = 0
        if option_list:
            _, option_total, option_fail = cal_option_acc(option_list)
            option_correct = round(cal_option_acc(option_list)[0] * option_total) if option_total > 0 else 0

        # open-ended: based on score field, score=1 is correct, score=-1 is parse failure
        oe_total = len(open_ended_list)
        oe_fail = sum(1 for it in open_ended_list if it.get("score", -1) == -1)
        oe_correct = sum(1 for it in open_ended_list if it.get("score", -1) == 1)

        # merged statistics
        total = option_total + oe_total
        fail = option_fail + oe_fail
        acc = round((option_correct + oe_correct) / total, 3) if total > 0 else 0

        return {"total": total, "fail": fail, "acc": acc}

    # "all" dimension: compute directly on all samples
    evaluation["all"] = eval_merged_items(multi_angle_results["all"])

    for angle in ["l0_category", "l1_category"]:
        for cate, item_list in multi_angle_results[angle].items():
            evaluation[angle][cate] = eval_merged_items(item_list)

            # compute bias metric for Potential Risk Reasoning as a whole
            if angle == "l0_category" and cate == "Potential Risk Reasoning":
                option_list = [it for it in item_list if it.get("question_type") == "option"]
                bias_score, bias_total = cal_option_bias(option_list)
                evaluation[angle][cate]["option_bias"] = bias_score
                evaluation[angle][cate]["option_bias_total"] = bias_total

    json_name = os.path.basename(test_path)
    os.makedirs(os.path.join(root, "EVAL"), exist_ok=True)
    with open(os.path.join(root, "EVAL", json_name), 'w', encoding="utf-8") as f2:
        json.dump(evaluation, f2, indent=4, ensure_ascii=False)
    print(f"{json_name} eval done!")


if __name__ == "__main__":
    # initialize LLM judge predictor (for open-ended evaluation)
    predictor = APIPredictor(API_KEY, API_BASE_URL, JUDGE_MODEL)

    for model in ["claude_sonnet4.6", "claude-opus4.7", "gemini-3.1-pro-preview", "gpt-5.5", "gpt-5.4", "glm-5.2", "glm-4.7", "MiniMax-M2.1", "MiniMax-M2.5", "InternVL3_5-8B", "MiMo-VL-7B-RL", "llava-v1.6-mistral-7b-hf", "kimi-k2.6", "kimi-k2-thinking", "qwen3-vl-8b-instruct", "qwen3.5-plus", "qwen3.7-plus"]:
        binary_eval(test_path=os.path.join(root, "TEST", f"{model}_final.json"),
                    predictor=predictor)
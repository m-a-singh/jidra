import re
import yaml
from datasets import load_dataset


def extract_files_from_patch(patch_text):
    if not patch_text:
        return []
    files = re.findall(r"^\+\+\+ b/(.*)", patch_text, re.MULTILINE)
    return list(set(files))


print("Loading SWE-bench Multilingual...")
# Pull the official dataset
dataset = load_dataset("SWE-bench/SWE-bench_Multilingual", split="test")

ts_java_cases = []

for item in dataset:
    # Filter for Java or JS/TS languages
    # Note: Depending on the metadata schema, you can also filter by repo name or 'language' key if present
    repo_name = item["repo"].lower()

    # We parse out the expected file targets
    expected_files = extract_files_from_patch(item.get("patch", ""))

    # Simple check to see if it's a Java/TS/JS instance based on file extensions
    is_target_lang = any(f.endswith((".scala")) for f in expected_files)

    if is_target_lang:
        case = {
            "id": item["instance_id"],
            "repo": item["repo"],
            "commit": item["base_commit"],
            "query": item["problem_statement"],
            "expected": expected_files,
        }
        ts_java_cases.append(case)

# Save to your local YAML format
with open("ts_scala_test_cases.yaml", "w") as f:
    yaml.dump(ts_java_cases, f, default_flow_style=False, sort_keys=False)

print(f"Saved {len(ts_java_cases)} Java/TypeScript test cases!")

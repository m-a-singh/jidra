# validate_jidra_analysis.py — Usage Examples

## Quick Start

### 1. Using with the Local Graph

```bash
cd /Users/akhil.singh/Workflows/Personal/chicha_v4/scripts/jidra

# Load the validated graph
python3 validate_jidra_analysis.py --graph jidra/output/graph_validated.jsonl
```

Then in the interactive menu:
```
Choice [1-6]: 2

📋 Loading methods from graph...

Available methods:
  1. com.myorg.App#main
  2. com.myorg.Stack#new
  3. ...

Select method [1-5] (or 'search <name>'): 1
✓ Loaded: com.myorg.App#main
```

### 2. Search for a Specific Method

```bash
python3 validate_jidra_analysis.py --graph jidra/output/graph_validated.jsonl

# Then at the menu, use search:
Choice [1-6]: 2

Select method [1-5] (or 'search <name>'): search Stack
✓ Loaded: com.myorg.Stack#init
```

### 3. Use Different Models

Compare Opus vs Sonnet:

```bash
# First run with Opus (expensive but most capable)
python3 validate_jidra_analysis.py --graph jidra/output/graph_validated.jsonl --model claude-opus-4-7

# Try a query and note the tokens/cost

# Then switch to Sonnet
Choice [1-6]: 4
Select [1-3]: 2
✓ Model changed to: claude-sonnet-4-6

# Run the same query and compare results
```

### 4. Analyze Custom Code

If you have another graph.jsonl from a different codebase:

```bash
# Your own project
python3 validate_jidra_analysis.py \
  --graph /path/to/your/codebase/.jidra/graph_validated.jsonl \
  --repo /path/to/your/codebase

# Browse and analyze your project's methods
```

### 5. Demo Mode (No Graph Required)

```bash
# This works without any graph file
python3 validate_jidra_analysis.py

Choice [1-6]: 2
Using demo query: 'Analyze this method...' (no graph loaded)

# Uses the sample PaymentService context
```

---

## Real Example Session

### Step 1: Start the script
```bash
$ python3 validate_jidra_analysis.py --graph jidra/output/graph_validated.jsonl --model claude-opus-4-7

======================================================================
JIDRA Analysis Validator — Interactive Query Comparison
======================================================================

Model: claude-opus-4-7
Graph: jidra/output/graph_validated.jsonl
This tool compares Claude API token usage WITH and WITHOUT JIDRA context.

Options:
  1. Enter a custom query
  2. Pick a method from graph & analyze
  3. Show results summary
  4. Change model
  5. Set/change graph file
  6. Exit

Choice [1-6]: 2
```

### Step 2: Pick a method
```
📋 Loading methods from graph...

Available methods:
  1. com.myorg.App
  2. com.myorg.Stack
  3. com.myorg.ServiceA#process
  4. com.myorg.ServiceB#execute
  5. com.myorg.Utils#validate

Select method [1-5] (or 'search <name>'): 3
✓ Loaded: com.myorg.ServiceA#process

[1/2] Calling Claude WITH JIDRA context...
      ✓ 1,523 input tokens, 287 output tokens, $0.024102
```

### Step 3: See the comparison
```
[2/2] Calling Claude WITHOUT JIDRA context...
      ✓ 9,142 input tokens, 301 output tokens, $0.138945

╒═════════════════╤═════════════════╤════════════════════╤═════════════════════════╕
│ Metric          │ With JIDRA      │ Without JIDRA      │ Difference              │
╞═════════════════╪═════════════════╪════════════════════╪═════════════════════════╡
│ Input Tokens    │ 1,523           │ 9,142              │ -7,619 (83.3%)          │
│ Output Tokens   │ 287             │ 301                │ -14                     │
│ Thinking Tokens │ 0               │ 0                  │ 0                       │
│ Cost            │ $0.024102       │ $0.138945          │ $0.114843 (82.7%)       │
│ Latency (s)     │ 1.45s           │ 1.67s              │ -0.22s                  │
│ Stop Reason     │ end_turn        │ end_turn           │ -                       │
╘═════════════════╧═════════════════╧════════════════════╧═════════════════════════╛

📊 Summary:
  JIDRA:         $0.024102
  Without JIDRA: $0.138945
  Savings:       $0.114843 (82.7%)
```

### Step 4: Run multiple queries
```
Choice [1-6]: 1

Enter your query: What are the potential performance bottlenecks?
Enter JIDRA context (or press Enter for sample): 
Using sample JIDRA context (PaymentService)

[1/2] Calling Claude WITH JIDRA context...
      ✓ 1,687 input tokens, 402 output tokens, $0.031892

[2/2] Calling Claude WITHOUT JIDRA context...
      ✓ 8,956 input tokens, 418 output tokens, $0.139231

... (comparison table)

Choice [1-6]: 1

Enter your query: Identify potential null pointer exceptions
...
```

### Step 5: Review summary
```
Choice [1-6]: 3

====================================================================
VALIDATION RESULTS SUMMARY
====================================================================

╒───╤──────────────────────────╤───────────────┬────────────────┬──────────┬──────────────╕
│ # │ Query                    │ JIDRA Tokens  │ Without Tokens │ Reduction│ Savings      │
╞═══╪══════════════════════════╪═══════════════╪════════════════╪══════════╪══════════════╡
│ 1 │ Analyze this method for...│ 1,523         │ 9,142          │ 83.3%    │ $0.114843    │
│ 2 │ What are the potential...  │ 1,687         │ 8,956          │ 81.2%    │ $0.107339    │
│ 3 │ Identify potential null... │ 1,542         │ 9,087          │ 83.0%    │ $0.113982    │
╘═══╧══════════════════════════╧═══════════════╧════════════════╧══════════╧══════════════╛

Total input tokens saved: 21,260
Total cost savings: $0.336164
```

---

## Finding Graph Files

### This Repository
```bash
# Validated graph (recommended)
/Users/akhil.singh/Workflows/Personal/chicha_v4/scripts/jidra/jidra/output/graph_validated.jsonl

# Test graphs
/Users/akhil.singh/Workflows/Personal/chicha_v4/scripts/jidra/jidra/output/graph_test.jsonl
```

### Your Own Codebase
If you've run JIDRA on your own code:

```bash
# Extract generates a graph in .jidra/
cd /path/to/your/repo
python3 -m jidra.cli extract --input . --output .jidra/graph.jsonl

# Then use it:
python3 validate_jidra_analysis.py --graph .jidra/graph.jsonl
```

---

## Troubleshooting

### "File not found: graph.jsonl"
Make sure the path is correct:
```bash
# Absolute path
python3 validate_jidra_analysis.py --graph /Users/akhil.singh/.../graph_validated.jsonl

# Relative path (from script directory)
cd /Users/akhil.singh/Workflows/Personal/chicha_v4/scripts/jidra
python3 validate_jidra_analysis.py --graph jidra/output/graph_validated.jsonl
```

### "No methods found in graph"
The graph file might be empty or in a different format. Check it:
```bash
# Count methods
grep '"node_type": "method"' jidra/output/graph_validated.jsonl | wc -l

# Show first few lines
head -5 jidra/output/graph_validated.jsonl | python3 -m json.tool
```

### "ANTHROPIC_API_KEY not set"
Set your API key first:
```bash
export ANTHROPIC_API_KEY="sk-..."
python3 validate_jidra_analysis.py --graph jidra/output/graph_validated.jsonl
```

---

## Repository Info

| Property | Value                                                           |
|----------|-----------------------------------------------------------------|
| Repository | JIDRA (Java Intelligent Diagnostic & Reasoning Agent)           |
| Location | `/Users/akhil.singh/Workflows/Personal/chicha_v4/scripts/jidra` |
| Main Remote | `https://github.com/akhilsinghcodes/JIDRA.git`                  |
| Public Mirror | `https://github.com/akhilsinghcodes/jidra.git`                  |
| Current Branch | `feature/re_index`                                              |

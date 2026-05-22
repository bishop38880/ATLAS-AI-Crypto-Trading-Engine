import ast
import os
import sys

def check_function_lengths(directory="."):
    violations = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                with open(path, "r", encoding="utf-8") as f:
                    try:
                        tree = ast.parse(f.read(), filename=path)
                        for node in ast.walk(tree):
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                # Calculate lines of code in function
                                if node.end_lineno is None:
                                    continue
                                length = node.end_lineno - node.lineno
                                if length > 40:
                                    violations.append(f"{path}:{node.lineno} - {node.name} is {length} lines.")
                    except SyntaxError:
                        print(f"Syntax error in {path}")
                        sys.exit(1)
    return violations

if __name__ == "__main__":
    print("Running AST 40-Line Limit Validation...")
    # Scan main directories
    all_violations = []
    for target_dir in ["prometheus", "atlas", "hydra"]:
        if os.path.exists(target_dir):
            all_violations.extend(check_function_lengths(target_dir))
    
    if all_violations:
        print("CRITICAL FAIL: 40-Line Limit Breached:")
        for v in all_violations:
            print(f"  - {v}")
        sys.exit(1)
    else:
        print("AST Validation Passed: All functions <= 40 lines.")
        sys.exit(0)
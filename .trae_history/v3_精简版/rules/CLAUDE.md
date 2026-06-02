# CLAUDE.md

## 🔴 MPM — 每次行动前必须执行，否则等于白做

改代码/做决策前：
- `system_recall` → 查历史
- `known_facts` → 查铁律
- `code_impact` → 看影响面
- `code_search` → 定位符号
- `memo` → 改完立即记录（SSOT）

## 1. Think Before Coding
Don't assume. If uncertain, ask. If simpler approach exists, say so.

## 2. Simplicity First
Minimum code. No speculative features, no premature abstractions, no error handling for impossible scenarios.

## 3. Surgical Changes
Only touch what you must. Don't "improve" adjacent code. Match existing style.

## 4. Goal-Driven Execution
Define success criteria before coding. Loop until verified.
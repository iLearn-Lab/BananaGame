---
name: frontend-optimization-plan
description: Create practical frontend code optimization plans with measurable goals and step-by-step execution. Use when the user asks for frontend performance optimization, rendering optimization, bundle size reduction, runtime speed improvements, or code quality optimization plans.
---

# Frontend Optimization Plan

## Goal
Produce a concrete optimization方案 that is measurable, low risk, and easy to implement in phases.

## Output Format
Use this structure:

```markdown
# 前端代码优化方案

## 1. 现状与目标
- 当前问题:
- 目标指标:

## 2. 优化优先级（P0/P1/P2）
- P0:
- P1:
- P2:

## 3. 具体优化动作
### 3.1 渲染性能
### 3.2 资源与打包
### 3.3 网络请求
### 3.4 代码结构与可维护性

## 4. 验证方案
- 指标:
- 验证步骤:
- 回归检查:

## 5. 实施排期
- 第 1 周:
- 第 2 周:
- 第 3 周:
```

## Workflow

### Step 1: Baseline
- Gather a baseline before any change:
  - Core Web Vitals: LCP, INP, CLS
  - JS bundle size (initial + async chunks)
  - First screen render time
  - Slow component render cost
- If profiling tools are available, use devtools traces and framework profiler to identify hot paths.

### Step 2: Diagnose by Layer
- Rendering layer:
  - Detect unnecessary rerenders, large list rendering, expensive computed values.
- Resource layer:
  - Identify oversized images, duplicated dependencies, over-splitting or under-splitting chunks.
- Network layer:
  - Detect waterfall requests, no-cache APIs, blocking requests on first screen.
- Architecture layer:
  - Detect giant components, repeated business logic, weak type boundaries.

### Step 3: Plan with Priorities
- P0 (high impact, low risk, short cycle):
  - Remove obvious rerenders and memoize heavy calculations.
  - Lazy load routes and non-critical components.
  - Compress and modernize image formats.
  - Enable request caching and deduping.
- P1 (medium cycle):
  - Split monolithic components.
  - Introduce virtualized rendering for long lists.
  - Refactor state boundaries to reduce update fan-out.
- P2 (longer term):
  - Build performance budget gates in CI.
  - Introduce bundle analysis in regular checks.
  - Standardize architecture patterns for shared modules.

### Step 4: Define Acceptance Criteria
- Each action must map to one measurable indicator.
- Example:
  - Home page LCP: 3.5s -> <2.5s
  - Initial JS payload: 900KB -> <500KB
  - Long task count: -40%

### Step 5: Risk Control
- Roll out in small batches and keep feature flags for risky refactors.
- Add regression tests for critical flows before large structural changes.
- Avoid broad rewrites without baseline proof.

## Optimization Playbook

### Rendering Optimization
- Memoize costly pure computations.
- Use stable props and callbacks to prevent child rerenders.
- Use windowing for large lists.
- Defer non-critical UI updates.

### Bundle and Asset Optimization
- Use route-based code splitting.
- Move heavy third-party libs behind dynamic import.
- Tree-shake dead code and remove unused packages.
- Prefer modern image formats and responsive image sizing.

### Network Optimization
- Batch related requests when possible.
- Apply stale-while-revalidate or short-term cache for hot APIs.
- Preload only truly critical assets.
- Avoid duplicate requests via request-level cache keys.

### Maintainability Optimization
- Limit component responsibility and size.
- Extract reusable hooks/utilities for repeated logic.
- Enforce lint/type gates in CI.
- Track performance budgets as engineering constraints.

## Guardrails
- Do not propose optimization without metrics.
- Do not recommend full rewrites as first choice.
- Always include trade-offs (complexity, memory, readability).
- Keep terminology consistent: "render", "bundle", "cache", "latency".

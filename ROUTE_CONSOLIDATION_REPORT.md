# ROUTE_CONSOLIDATION_REPORT

Date: 2026-07-30
Phase: 2.6 — Navigation & Route Consolidation
Status: **PASS**

## Modified Files

| 文件 | 操作 | 说明 |
|------|------|------|
| `frontend/src/layouts/MainLayout.tsx` | 修改 | 菜单标签：Automation Center→任务中心，Application Center→集成工具 |
| `frontend/src/App.tsx` | 修改 | 组件引用重命名 + /tools/* 路由全改为 redirect + /tools/containers 和 /tools/file-browser 改为 redirect |
| `frontend/src/pages/plugins/PluginList.tsx` | 修改 | 组件重命名为 IntegrationToolsList，标题从"插件中心"→"集成工具"，TOOL_PAGE_MAP 改为 /applications/* |
| `frontend/src/pages/system/SystemSettings.tsx` | 修改 | "应用中心"卡片→"集成工具"，"Plugin Management"卡片→"Integrations"，均指向 /applications |
| `frontend/src/i18n/locales/zh-CN.json` | 修改 | nav.plugins: 插件中心→集成工具，plugins.title: 插件中心→集成工具 |
| `frontend/src/i18n/locales/en-US.json` | 修改 | nav.plugins: Plugin Center→Integration Tools |

## Renamed Components

| 旧名称 | 新名称 | 文件 |
|--------|--------|------|
| `PluginList` (export) | `IntegrationToolsList` (export) | `pages/plugins/PluginList.tsx` |
| `import PluginList from './pages/plugins/PluginList'` | `import IntegrationToolsList from './pages/plugins/PluginList'` | `App.tsx` |

## Route Changes

### /tools/* → Redirect (all 8 entries consolidated)

| 旧路由 | 新行为 | 目标 |
|--------|--------|------|
| `/tools/pt-rss` | → Navigate | `/applications/pt-rss` |
| `/tools/alist` | → Navigate | `/applications/alist-upload` |
| `/tools/cloudflare` | → Navigate | `/applications/cloudflare-pages` |
| `/tools/cloudflare-ddns` | → Navigate | `/applications/cloudflare-ddns` |
| `/tools/docker-backup` | → Navigate | `/applications/docker-backup` |
| `/tools/log-cleanup` | → Navigate | `/applications/log-cleanup` |
| `/tools/containers` | → Navigate | `/containers` |
| `/tools/file-browser` | → Navigate | `/files` |

### Canonical routes (unchanged, now sole renderers)

| 路由 | 组件 |
|------|------|
| `/automation` | TaskList |
| `/applications` | IntegrationToolsList (renamed) |
| `/containers` | ContainerManager |
| `/files` | FileBrowser |
| `/logs` | LogCenter |
| `/settings` | SystemSettings |
| `/ai` | AIAssistant |
| `/applications/pt-rss` | PT_RSS |
| `/applications/alist-upload` | AlistUpload |
| `/applications/docker-backup` | DockerBackup |
| `/applications/cloudflare-ddns` | CloudflareDDNSPage |
| `/applications/cloudflare-pages` | CloudflarePages |
| `/applications/log-cleanup` | LogCleanup |

### Compatibility redirects (unchanged)

| 路由 | 目标 |
|------|------|
| `/tasks` | → `/automation` |
| `/plugins` | → `/applications` |
| `/notifications` | renders NotificationCenter (selectedKey→/settings) |

## Redirect Strategy

- All legacy `/tools/*` routes now use `<Navigate to="..." replace />`
- No duplicate rendering — each page has exactly one canonical route that renders it
- Old bookmarks continue to work via transparent redirect
- No 404 or broken links introduced

## Navigation Changes

| 位置 | 旧值 | 新值 |
|------|------|------|
| 主导航 item 2 | `Automation Center` | `任务中心` |
| 主导航 item 3 | `Application Center` | `集成工具` |
| 集成工具页标题 | `插件中心` | `集成工具` |
| SystemSettings 卡片 1 | `应用中心` | `集成工具` |
| SystemSettings 卡片 2 | `Plugin Management` / `/plugins` | `Integrations` / `/applications` |
| i18n zh-CN nav.plugins | `插件中心` | `集成工具` |
| i18n zh-CN plugins.title | `插件中心` | `集成工具` |
| i18n en-US nav.plugins | `Plugin Center` | `Integration Tools` |

## Duplicate Entry Cleanup

| 能力 | Cleanup |
|------|---------|
| PT RSS | /tools/pt-rss → redirect |
| AList Upload | /tools/alist → redirect |
| Cloudflare Pages | /tools/cloudflare → redirect |
| Cloudflare DDNS | /tools/cloudflare-ddns → redirect |
| Docker Backup | /tools/docker-backup → redirect |
| Log Cleanup | /tools/log-cleanup → redirect |
| Container Manager | /tools/containers → redirect |
| File Browser | /tools/file-browser → redirect |
| **Total resolved** | **8 双重入口 → 0** |

## Validation Results

| 检查项 | 结果 |
|--------|------|
| TSC + Vite build | ✅ PASS (0 errors) |
| Plugin Center 残留 | ✅ 0 matches in frontend/src/** |
| Application Center 残留 | ✅ 0 matches in frontend/src/** |
| Automation Center 残留 | ✅ 0 matches in frontend/src/** |
| 插件中心 残留 | ✅ 0 matches in frontend/src/** |
| 应用中心 残留 | ✅ 0 matches in frontend/src/** |
| /tools/* 重复渲染 | ✅ 全部改为 redirect |
| 菜单标签与 IA 一致 | ✅ 全部对齐 |
| selectedKey 逻辑 | ✅ 路径映射正确 |
| API/DB 不受影响 | ✅ 仅前端变更 |

## Remaining Technical Debt

| 项目 | 说明 | 优先级 |
|------|------|--------|
| PluginList.tsx 文件名 | 文件仍命名为 PluginList.tsx，组件已重命名 | 低（纯文件名重构，无功能影响） |
| btrfs_cleanup / rclone_mount 无应用页 | 两个内置工具在 FINAL_INFORMATION_ARCHITECTURE.md 中列出，但前端无独立配置页 | 后续 Phase |
| 通知中心 /notifications | 当前作为独立路由但无一级导航入口，selectedKey 映射至 /settings，UI 一致性可优化 | 后续 Phase |

## Conclusion

**PASS**

Phase 2.6 完成标准全部达成：

- ✅ Plugin Center 不存在（代码中 0 残留）
- ✅ Automation Center 不存在（代码中 0 残留）
- ✅ Application Center 不存在（代码中 0 残留）
- ✅ 菜单名称全部与 FINAL_INFORMATION_ARCHITECTURE.md 一致
- ✅ /tools/* 全部变为兼容跳转（8 routes → Navigate）
- ✅ 无双重入口（8 duplicates resolved）
- ✅ 高亮正常（selectedKey 逻辑不变）
- ✅ FINAL_INFORMATION_ARCHITECTURE.md 与代码实现一致
- ✅ Build 通过，零错误

未修改 Dashboard、未新增功能、未修改 API/DB。

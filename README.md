# 数据共享授权到期与分区访问复核

供企业数据共享审计专员核对原始记录、处理差异并封存证据。

## 启动

```sh
docker compose up --build -d
docker compose exec -T app pytest -q
```

打开 http://localhost:18248 。首次进入自动运行仓内脏样例。工具条可重跑、切换样例或上传CSV。SQLite保存在命名卷records中，重建服务保留批次。

## 模块

- 1. 访问采信：`/accesses`，`/api/accesses`。
- 2. 资源映射：`/resources`，`/api/resources`。
- 3. 授权定版：`/grants`，`/api/grants`。
- 4. 冲突裁决：`/conflicts`，`/api/conflicts`。
- 5. 分区判定：`/partitions`，`/api/partitions`。
- 6. 访问对账：`/audit`，`/api/audit`。
- 7. 审计归档：`/seals`，`/api/seals`。
- 8. 共享额度占用：`/quotas`，`/api/quotas`。

## 核心规则

拒绝规则优先于允许，授权必须同时命中资源、分区和生效窗；显式拒绝不得被宽授权覆盖。

### 共享额度占用裁决

**裁决：到达时预留（reserve-on-arrival），而非执行后占用再回退。** 区域团队会重试与撤回，执行后记账在并发下必然超用；到达预留以先到先占守住总额度，撤回释放后由台账重算把额度让给排队请求。

- 台账为只追加事件溯源（`app/modules/m8_quotas/ledger.py`），结论只由 `(额度池限额, 事件序列)` 重放得到，同一输入永远得到同一份**每请求唯一占用结论**。
- 授权定版（M3）、访问对账（M6）、额度占用页（M8）与证据导出共用 `context["quota_ledger"]` 同一结论，不存在第二本账。
- 事件：`arrive` 到达预留、`retry` 重试（按 `request_id` 幂等，不新增占用）、`withdraw` 撤回（额度失效并触发排队重算）、`settle` 结算转占用、`settle_failed` 结算失败（保留旧版结论仅留痕）。
- 状态：`held` 已预留、`waiting` 排队、`occupied` 已占用、`withdrawn` 已撤回；非法事件隔离为 `rejected`，不污染额度。
- 参数 `parameters.quotas.limits` 配置额度池（缺省 `shared-default=8`），`parameters.quotas.events` 显式提供事件；未提供时每条采纳行合成一次单位额到达事件，旧样例无需改造。

## 演示

1. 打开首页，核对当次计算、差异和隔离原因。
2. 切换模块查看源行及裁决依据。
3. 重跑相同样例，批次号与行数保持一致。
4. 复核后导出一份证据CSV。版本已变化的复核提交返回409。

## 输入与输出

输入列见 `docs/DATA-DICTIONARY.md`。项目包含dirty.csv、clean.csv、legacy.csv，统一走实际导入路径。上传限制4MB，拒绝不带时区的时间。业务键按字符串保留，Decimal参与计算，空值不会当零。

`POST /api/runs` 接收sample或csv；`GET /api/runs`返回历史批次；`POST /api/runs/<id>/review`需要version与decision；`GET /api/runs/<id>/archive`返回唯一证据导出。

## 范围

样例均为虚构。单机复核工具不连接生产系统；不提供登录、远端数据库或自动支付。内存计算按4MB输入上限控制，SQLite事务保证结果完整写入。

## 目录

app为服务与规则，sample_data为输入，tests为测试，docs为定义与深度登记。

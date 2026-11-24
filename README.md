# 纸样实验室取样台

纸样实验室的制样员从当班任务单录入固定票序，在一台模拟冲切台上逐票制样。每次实际冲切消耗一片试片。质检员按原票核对克重和设备出处，班末固定一份可在重开后领取的质量账。

## 运行

需要 Python 3.11 和标准库。从项目根目录执行完整正常回归和演示。

```sh
python3 -B check_project.py
python3 -B demo.py
```

`samples/day.json` 给出有限日剧本。演示在两个临时 SQLite 库中使用公开业务入口，先让 A 的请求排队并丢回包。第一次观察流水为空且会话关闭受阻，A 待查，B 待办。旧请求晚到后产生缺测流水 41。重开后 A 领取 41，B 以自己的冲切取得 42 和 23 克。最后关闭连接并按原报告号领取。手算结果是两片试片、一条有效读值、合计 23 克。数据库及脚本步进均无随机依赖。

## 公开 API

| 操作 | 参数及结果 |
| --- | --- |
| `lab.open_archive(path)` | 打开档案库并执行幂等初始化，返回由调用方关闭的 SQLite 连接。`lab.initialize(db)` 可初始化已有的空闲连接。 |
| `worklist.shifts.register(db, shift_id, entries)` | `entries` 为按原顺序排列的 `(ticket_id, recipe)` 对。可用配方 `round`、`strip`。返回原班号，原样重登保留票序及进度。重复票号或非法配方抛 `ValueError`，同班号异参抛 `RegistrationConflict`，整批拒绝。 |
| `capture.dispatch.advance(db, shift_id, device)` | 推进一步。当前票已有尝试时返回原票视图，全部结票时返回 `None`。 |
| `capture.resume.resume(db, shift_id, device)` | 办理同一个未结原票。待查时返回 `pending`，查清无执行时补采原票，查清已执行时归档原流水。无尝试时可以开始该班的当前票。 |
| `archive.history.read_ticket(db, shift_id, ticket_id)` | 返回带固定原票的 `TicketView`，状态为 `todo`、`pending` 或 `closed`，只有已结状态携带 `terminal.cut`。未知票抛 `TicketNotFound`。 |
| `review.detail.build_shift_detail(db, shift_id)` | 返回按原票序排列的质检明细，可含待办或待查行。 |
| `review.close.close_shift(db, shift_id)` | 按班定稿，返回包含报告号、明细和总计的 `ShiftReport`。票未齐抛 `IncompleteShift` 并给出未结票号。重签返回原报告。 |
| `review.lookup.lookup_report(db, number)` | 按报告号读取固定快照。未知报告号抛 `ReportNotFound`。 |
| `worklist.shifts.register_shared(db, shift_id, paper_id, width, height, entries)` | 共纸班登记。`entries` 按票序给出 `(ticket_id, recipe, candidates)`，候选是纸格矩形。相同参数重登幂等；改纸号、尺寸、票序、配方或候选，或将同一纸号登记给另一班抛 `RegistrationConflict`；与普通班互相改登亦冲突。 |
| `capture.resume.resume_shared(db, shift_id, device)` | 办理共纸班当前票，返回 `ticket`/`status`/`terminal`。有完整排法即落刀并返回 `closed`，尚待查证返回 `pending`，无解返回 `blocked` 且保留当前票、不耗纸不消费读值。票齐返回 `None`。 |
| `device.press.Press.load_paper(paper_id, width, height)` | 在设备登记纸面，相同参数复用，异尺寸抛 `ValueError`。 |
| `device.press.Press.mark_damage(paper_id, cells)` | 持久标损格。未知纸号、越界或压到实切格抛 `ValueError`，整次不生效。 |

`device.press.Press(path, device_id=..., serial_floor=...)` 使用独立设备库，保存原始流水、关闭状态和初始流水前沿。重开同一数据库可省略已保存的设备参数，传入冲突值会拒绝。原始设备 API 支持开会话、按会话及配方提交、关闭会话和按会话读取，`Press.submit_cut` 在实际执行时调用传入的称量读取函数。`device.simulator.ScriptedDevice` 提供供 `advance` 和 `resume` 使用的设备端口。装载时完整校验整数克或空值。`LOSE_BEFORE_EXECUTION` 与 `LOSE_AFTER_EXECUTION` 交付相同的未知结果。`DEFER_EXECUTION` 把请求留待 `step(RELEASE_QUEUED, session_id)` 步进。`BLOCK_CLOSE`、`ALLOW_CLOSE`、`BLOCK_READ` 和 `ALLOW_READ` 控制会话查证。模拟脚本和未执行的排队请求属于运行中的模拟设备，档案连接关闭时可继续独立步进。实际冲切、设备出处和会话关闭结果持久留在设备库中。

## 持久边界和核账口径

每个档案库与同一台设备的独立库配对。从第一张票开始到最后一张票结清，冲切台归该班占用。其他班可登记和读取，尝试取样时会收到 `ShiftInProgress`。业务写入口使用空闲档案连接。读取入口可以加入调用方已有的读事务。未知班号在推进、明细及定稿时抛 `ShiftNotFound`。可以登记空任务单，其固定报告为零行和三项零值。

采集会在冲切前登记原票尝试。一条暂时为空的流水观察仍可能随后看到实际冲切。关闭会话且完整读取后，空范围才授权原票补采。已发生的无读值冲切会照常结票。归档提交失败会向调用方传递 SQLite 异常，重开后可继续办理原票。终局、流水唯一归属和票序推进在同一次档案事务中提交。已结票重见同一回执时返回原终局。设备关闭与实际执行串行，已封会话的晚到请求不会耗用试片或称量值。

质检明细沿原票序列出试片出处。每条已归属流水计一次冲切，非空整数克计一次有效读值并按整数累加。0 克计作有效读值，缺测保留出处且不进入读值计数和合计。班末报告保存提交时的明细与总计，后续班次定稿不会改变旧报告。任务单样例可作为后续导入的输入修改，已登记班次仍按原固定内容办理。

## 共纸冲切

共纸班与普通班共用原压机占用规则：一个班独占压机到最后一票结清。共纸班只能经 `resume_shared` 办理；对共纸班调用普通 `advance`/`resume`，或对受管会话调用普通 `Press.submit_cut`，都抛 `ValueError`。普通班、旧报告和原回归不受影响。

每次实际落刀前，本票与所有余票都要各取一个互不重叠、且避开标损和已冲格的候选。有完整排法时才落刀，设备在同一次设备事务里记下纸号与实切矩形连同流水。无完整排法返回 `blocked`，保留当前票且不耗样、不消费称量读值、不写流水。设备在落刀前会再按当前纸面复核整班可行性，排法失效时同样拒刀。

失答先按原会话查证：临时空读或不完整查证仍是 `pending`，确证未冲才能按现状重排，已冲必须按设备所记的实际位置实收。归档提交失败时，原票终局、位置与后票资格都不发布；重开续办接回已发生的原件而不重复落刀。共纸实切在原 `cut` 字段之外提供 `cut.paper_id` 与四项整数序列 `cut.rect`，并原样固定进报告行。

`samples/shared_paper.json` 与 `paper_fixture.py verify|demo` 演示标损、排法、失答与失败续办。`.pointwise-runtime.json` 的 `verify` 跑完整原回归加共纸分核，`demo` 展示停源后按原报告号领取的整班。

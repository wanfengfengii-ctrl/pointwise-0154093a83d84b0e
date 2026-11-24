# 共纸冲切交接

制样员按原任务单行序办理。每张纸只用于一个班，每个班只登记一张纸。共纸班和普通班共用原压机占用规则。

## 登记与标损

- `Press.load_paper(paper_id, width, height)` 在设备登记纸号与尺寸。相同参数复用已有纸面，改用不同尺寸抛 `ValueError`。现场可以先在设备装纸，再登记站端班票。
- `worklist.shifts.register_shared(db, shift_id, paper_id, width, height, entries)` 返回原班号。`entries` 按票序给出 `(ticket_id, recipe, candidates)`，至少一票，每票至少一个候选。票号和配方校验沿用 README。相同参数重登保留进度。改纸号、尺寸、票序、配方或候选列表，或将同一纸号登记给另一班，均抛 `RegistrationConflict`。普通班与共纸班不能互相改登。非法参数抛 `ValueError`，登记整批生效。
- 格坐标从 0 开始，`x` 为列，`y` 为行。尺寸是正整数。每个候选是 `(x, y, w, h)`，宽高为正整数，占用 `[x,x+w) × [y,y+h)` 内的整数格。候选须完全位于原纸内。候选已过工艺筛选，矩形可以贴边，共用格即冲突。
- `Press.mark_damage(paper_id, cells)` 在设备持久标损，`cells` 为 `(x, y)` 序列，原样重标可复用。未知纸号、越界或已有实切占用的格抛 `ValueError`，整次标记不生效。标损可在采集请求排队期间或落刀之后发生，重开设备库仍有效。

## 续办与领取

`capture.resume.resume_shared(db, shift_id, device)` 办理当前票，使用现有 `ScriptedDevice`。返回对象的 `ticket` 为本次票，`status` 为 `closed`、`pending` 或 `blocked`。已实收才返回 `closed`，尚待查证返回 `pending`。纸面稳定、设备可用且无额外故障时，有解须办成当前票，无解返回 `blocked`。`blocked` 时保留当前票，设备不耗纸、不消费称量读值、不写实切流水。该班票已齐时返回 `None`。对共纸班使用普通 `advance` 或 `resume`，或对其受管会话调用普通 `Press.submit_cut`，抛 `ValueError`。

每次实际落刀时，本票和余票须能各取一处互不重叠且避开标损和已冲格的候选。有多种完整排法时任选。设备一次登记实际消耗的矩形和对应流水。失答时沿原会话查证。临时空读和不完整查证仍须等待，确证未冲才能按现状重排，已冲必须按实际位置实收。归档提交失败沿原机制传播 SQLite 异常，原票终局、位置和后续票资格均不发布，重开续办可接回已发生的原件。

`DEFER_EXECUTION` 让共纸请求停到 `step(RELEASE_QUEUED, session_id)`。可从 `device.submissions[-1].session_id` 取本次会话号。步进返回实际冲切事实，未执行则返回 `None`。`LOSE_AFTER_EXECUTION` 用于确认实切先于失答。共纸实切在 `Press.cuts()`、`Press.read_session()`、`read_ticket()` 的终局及固定报告行中沿用原 `cut` 字段，并提供 `cut.paper_id` 与四项整数序列 `cut.rect`。使用原 `close_shift` 定稿和 `lookup_report` 领报。票齐才可定稿，重签复领同一报告，原票序、实切位置、原流水、设备出处、克重与统计一并固定。0 克计有效读值，缺测只计冲切。

## 样例与运行

`samples/shared_paper.json` 中的纸面是一行七格。C 只可取格 1，排在最前的 A 不能因此取格 0、1。请求排队后只损格 6，最终 B 须取格 0，C 须取格 1，A 可取格 2、3 或格 4、5。若在首笔落刀前格 0、6 均损，A 返回 `blocked`，设备流水仍为空。另一支先损 6，A 落刀并失答后再损 0，重开并在首轮实收提交失败后续办，仍应认回 A 的实切位置和流水 41，随后 B 受阻。可行支路按 0 克、缺测、23 克完成，停源领报的统计为 3 次冲切、2 条有效读值、23 克。

`python3 -B paper_fixture.py selfcheck` 使用普通班验证原有延迟执行、失答查证及提交失败夹具。`python3 -B paper_fixture.py verify` 在实现共纸功能后运行原回归和上述业务分支，失败退出非零。它复用 `tests.support.fail_terminal_commit` 注入档案终局提交失败。若采用其他终局存储，可把注入接入实际终局提交并保持失败后的可见结果检查。`python3 -B paper_fixture.py demo` 在实现后展示排队期间标损、后续冲切和移去设备库后的实领报告。

在现有 `.pointwise-runtime.json` 中接入共纸核验和演示，可使用上述 `verify`、`demo` 命令。清单沿用版本 1 和 `cli` 类型，`setup` 是准备命令的参数数组列表，`verify`、`demo` 各是一个命令参数数组。核验入口须包含全部原回归，任一业务核对失败须退出非零。演示输出实际领取的纸号、位置、原流水、出处、克重和总计。

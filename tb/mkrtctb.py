"""rtc 的行为测试台：分频走得对、闹钟只在到点时响、写一清零、数组不互相盖。

要紧的是「没配过的闹钟不该响」：比较值与计数器复位都是 0，一使能就相等，
电平比较会让每个没配过的闹钟当场全响。timer 上出过同一个错。

认矩阵：`alarms` 与 `alarm` 从这一点的旋钮来。特性关掉时期望反过来——
闹钟与状态都读回零，中断线始终不抬。
"""
import json
import pathlib
import sys

out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
out.mkdir(parents=True, exist_ok=True)
cfg = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
label = cfg.get("label", "")
k = cfg.get("knobs", {})
alarms = int(k.get("alarms", 1))
alarm = bool(k.get("alarm", True))

A0 = 10          # 第一个闹钟的时刻
SCALE = 4        # 十六拍走一格，轮询才来得及

if alarm:
    body = f'''  // 配好第一个闹钟再使能。其余闹钟一律留在 0：它们不该响。
  rule setup (ph == Setup);
    case (s)
      0: wr(8'h20, {A0});           // alarm[0] 低字
      1: wr(8'h24, 0);              // 高字
      2: wr(8'h00, 32'h0000100{SCALE:X});   // en + scale
      default: ph <= Early;
    endcase
    if (s < 3) s <= s + 1; else s <= 0;
  endrule

  // 到点之前状态位必须一直是零——没配过的闹钟在这里会露馅
  rule early (ph == Early);
    let x <- d.regs.access(RegReq {{ addr: 8'h08, write: False,
                                     wdata: 0, wstrb: 4'hF }});
    if (x.rdata >= {A0}) ph <= Late;
    else if (irqSeen[1]) begin
      $display("FAIL an alarm fired at count %0d, none was configured for it",
               x.rdata);
      bad <= True;
      ph <= Done;
    end
  endrule

  // 到点之后只有第 0 个该响
  rule late (ph == Late);
    let x <- d.regs.access(RegReq {{ addr: 8'h40, write: False,
                                     wdata: 0, wstrb: 4'hF }});
    if (x.rdata[0] == 1) begin
      Bool wrong = False;
      if ((x.rdata[3:0] & 4'hE) != 0) begin
        $display("FAIL other alarms fired too: %02h", x.rdata[3:0]);
        wrong = True;
      end
      if (wrong) bad <= True;
      ph <= Settle;
    end
  endrule

  // 中断线是从状态位组合出来的，采样规则要下一拍才latch到，所以隔一拍再看
  rule settle (ph == Settle);
    if (!irqSeen[1]) begin
      $display("FAIL the alarm fired but the interrupt line never rose");
      bad <= True;
    end
    ph <= Clear;
  endrule

  rule clear (ph == Clear);
    wr(8'h40, 32'h0000000F);        // 写一清零
    ph <= CheckClear;
  endrule

  rule checkClear (ph == CheckClear);
    let x <- d.regs.access(RegReq {{ addr: 8'h40, write: False,
                                     wdata: 0, wstrb: 4'hF }});
    if (x.rdata[0] != 0) begin
      $display("FAIL status not cleared by write one: %08h", x.rdata);
      bad <= True;
    end
    ph <= Done;
  endrule
'''
    verdict = "the prescaler counts, only the configured alarm fires, write one clears"
else:
    body = '''  rule setup (ph == Setup);
    case (s)
      0: wr(8'h20, 32'h000000FF);   // 闹钟关着，写了也该读回零
      1: wr(8'h00, 32'h00001004);   // en + scale
      default: ph <= Early;
    endcase
    if (s < 2) s <= s + 1; else s <= 0;
  endrule

  rule early (ph == Early);
    let x <- d.regs.access(RegReq { addr: 8'h20, write: False,
                                    wdata: 0, wstrb: 4'hF });
    if (x.rdata != 0) begin
      $display("FAIL alarms are off but the compare kept a value: %08h", x.rdata);
      bad <= True;
    end
    ph <= Late;
  endrule

  rule late (ph == Late);
    let x <- d.regs.access(RegReq { addr: 8'h40, write: False,
                                    wdata: 0, wstrb: 4'hF });
    if (x.rdata != 0) begin
      $display("FAIL alarms are off but the status is not zero: %08h", x.rdata);
      bad <= True;
    end
    ph <= Clear;
  endrule

  // 计数器照样要走：分频与闹钟是两件事
  rule clear (ph == Clear);
    let x <- d.regs.access(RegReq { addr: 8'h08, write: False,
                                    wdata: 0, wstrb: 4'hF });
    if (x.rdata >= 10) ph <= CheckClear;
  endrule

  rule checkClear (ph == CheckClear);
    if (irqSeen[1]) begin
      $display("FAIL alarms are off but the interrupt line went high");
      bad <= True;
    end
    ph <= Done;
  endrule
'''
    verdict = "the prescaler counts and the alarm gate really gates"

txt = f'''package Rtc{label}Tb;

import RegIf::*;
import Rtc::*;

// 由 tb/mkrtctb.py 生成，勿手改。这一点：alarms={alarms} alarm={alarm}

typedef enum {{ Setup, Early, Late, Settle, Clear, CheckClear, Done }}
  Phase deriving (Bits, Eq);

(* synthesize *)
module mkRtc{label}Tb(Empty);
  RtcIfc#(8, 32, {alarms}) d <- mkRtc(
      RtcCfg {{ alarm: {"True" if alarm else "False"} }});

  Reg#(Phase)    ph  <- mkReg(Setup);
  Reg#(Bit#(8))  s   <- mkReg(0);
  Reg#(Bit#(32)) cyc <- mkReg(0);
  Reg#(Bool)     bad <- mkReg(False);
  // 采样规则每拍都跑，凡是它写、检查规则读的量都得用 CReg
  Reg#(Bool)     irqSeen[2]  <- mkCReg(2, False);

  rule sample;
    if (d.irq) irqSeen[0] <= True;
  endrule

  rule tick_;
    cyc <= cyc + 1;
    if (cyc > 40000) begin
      $display("TIMEOUT in phase %0d", pack(ph));
      $finish(1);
    end
  endrule

  function Action wr(Bit#(8) a, Bit#(32) v) = action
    let _ <- d.regs.access(RegReq {{ addr: a, write: True,
                                     wdata: v, wstrb: 4'hF }});
  endaction;

{body}
  rule fin (ph == Done);
    if (bad) $display("FAILED");
    else $display("PASS rtc: {verdict}");
    $finish(bad ? 1 : 0);
  endrule
endmodule

endpackage
'''

(out / f"Rtc{label}Tb.bsv").write_text(txt, encoding="utf-8")
print(f"  rtc 行为测试台就位：alarms={alarms} alarm={alarm}")

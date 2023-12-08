#!/usr/bin/env python3

#
# This file is part of LiteX-Boards.
#
# Copyright (c) 2023 MoTeC
# Copyright (c) 2023 Gwenhael Goavec-Merou <gwenhael@enjoy-digital.fr>
# Copyright (c) 2017-2023 Florent Kermarrec <florent@enjoy-digital.fr>

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.generic_platform import *
from litex_boards.platforms import efinix_titanium_ti60_f225_dev_kit

from litex.soc.cores.clock import TITANIUMPLL

from litex.soc.integration.soc_core import *
from litex.soc.integration.soc import SoCRegion
from litex.soc.integration.builder import *

from litex.soc.interconnect import wishbone

from liteiclink.serwb.phy import SERWBPHY
from liteiclink.serwb.genphy import SERWBPHY as SERWBLowSpeedPHY
from liteiclink.serwb.core import SERWBCore

from litescope import LiteScopeAnalyzer

# IOs ----------------------------------------------------------------------------------------------
master_connector = "P2"
slave_connector  = "P3"
serwb_io = [
    ("serwb_master", 0,
        Subsignal("clk_p", Pins(f"{master_connector}:2"),  IOStandard("1.8_V_LVCMOS")),
        Subsignal("clk_n", Pins(f"{master_connector}:4"),  IOStandard("1.8_V_LVCMOS")),
        Subsignal("tx_p",  Pins(f"{master_connector}:8"),  IOStandard("1.8_V_LVCMOS")),
        Subsignal("tx_n",  Pins(f"{master_connector}:10"), IOStandard("1.8_V_LVCMOS")),
        Subsignal("rx_p",  Pins(f"{master_connector}:14"), IOStandard("1.8_V_LVCMOS")),
        Subsignal("rx_n",  Pins(f"{master_connector}:16"), IOStandard("1.8_V_LVCMOS")),
    ),

    ("serwb_slave", 0,
        Subsignal("clk_p", Pins(f"{slave_connector}:2"),  IOStandard("1.8_V_LVCMOS")),
        Subsignal("clk_n", Pins(f"{slave_connector}:4"),  IOStandard("1.8_V_LVCMOS")),
        Subsignal("tx_p",  Pins(f"{slave_connector}:14"), IOStandard("1.8_V_LVCMOS")),
        Subsignal("tx_n",  Pins(f"{slave_connector}:16"), IOStandard("1.8_V_LVCMOS")),
        Subsignal("rx_p",  Pins(f"{slave_connector}:8"),  IOStandard("1.8_V_LVCMOS")),
        Subsignal("rx_n",  Pins(f"{slave_connector}:10"), IOStandard("1.8_V_LVCMOS")),
    ),
]

# CRG ----------------------------------------------------------------------------------------------

class _CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq):
        self.cd_sys         = ClockDomain()
        self.cd_serwb_phy   = ClockDomain()
        self.cd_serwb_phy4x = ClockDomain()

        # # #

        # Clk/Rst.
        clk25 = platform.request("clk25")
        rst_n = platform.request("user_btn", 0)

        # PLL
        self.pll = pll = TITANIUMPLL(platform)
        self.comb += pll.reset.eq(~rst_n)
        pll.register_clkin(clk25, 25e6)
        # You can use CLKOUT0 only for clocks with a maximum frequency of 4x
        # (integer) of the reference clock. If all your system clocks do not fall within
        # this range, you should dedicate one unused clock for CLKOUT0.
        pll.create_clkout(None, 25e6)
        pll.create_clkout(self.cd_sys,                sys_clk_freq, with_reset=True, phase=0,  name="sys")
        pll.create_clkout(self.cd_serwb_phy,   int(sys_clk_freq*1), with_reset=True, phase=0,  name="serwb_phy")
        pll.create_clkout(self.cd_serwb_phy,   int(sys_clk_freq*4), with_reset=True, phase=90, name="serwb_phy4x")

# SerWBTestSoC ------------------------------------------------------------------------------------

class SerWBTestSoC(SoCMini):
    mem_map = {
        "serwb": 0x30000000,
    }
    mem_map.update(SoCMini.mem_map)

    def __init__(self, platform, sys_clk_freq=100e6, with_analyzer=True):
        # CRG --------------------------------------------------------------------------------------
        self.crg = _CRG(platform, sys_clk_freq)

        # SoCMini ----------------------------------------------------------------------------------
        SoCMini.__init__(self, platform, sys_clk_freq,
            csr_data_with = 32,
            ident         = "LiteICLink SerWB bench on Efinix Titanium Ti60 F225 Dev Kit",
            ident_version = True,
            with_uart     = False,
            with_jtagbone = True)

        # SerWB ------------------------------------------------------------------------------------
        # SerWB simple test with a SerWB Master added as a Slave peripheral to the SoC and connected
        # to a SerWB Slave with a SRAM attached. Access to this SRAM is then tested from the main
        # SoC through SerWB:
        #                   +--------+    +-------+    +-------+    +------+
        #                   |        |    |       +-ck->       |    |      |
        #                   |  Test  +----+ SerWB +-tx-> SerWB +----> Test |
        #                   |   SoC  | WB |Master |    |Slave  | WB | SRAM |
        #                   |        +<---+       <-rx-+       <----+      |
        #                   +--------+    +-------+    +-------+    +------+
        # ------------------------------------------------------------------------------------------

        # SerWB Master ---------------------------------------------------------------------------
        # PHY
        serwb_master_phy = SERWBPHY(
            device       = self.platform.device,
            pads         = self.platform.request("serwb_master"),
            mode         = "master",
            clk          = "serwb_phy",
            clk4x        = "serwb_phy4x",
            clk_ratio    = "1:2",
        )
        self.submodules.serwb_master_phy = serwb_master_phy

        # Core
        serwb_master_core = SERWBCore(serwb_master_phy, self.clk_freq, mode="slave",
            etherbone_buffer_depth = 1,
            tx_buffer_depth        = 8,
            rx_buffer_depth        = 8
        )
        self.submodules += serwb_master_core

        # Connect as peripheral to main SoC.
        self.bus.add_slave("serwb", serwb_master_core.bus, SoCRegion(origin=0x30000000, size=8192))

        # SerWB Slave ----------------------------------------------------------------------------
        # PHY
        serwb_slave_phy = SERWBPHY(
            device       = self.platform.device,
            pads         = self.platform.request("serwb_slave"),
            mode         = "slave",
            clk_ratio    = "1:2",
        )
        self.clock_domains.cd_serwb = ClockDomain()
        self.comb += self.cd_serwb.clk.eq(serwb_slave_phy.serdes.clocking.refclk)
        self.specials += AsyncResetSynchronizer(self.cd_serwb, ResetSignal("sys"))
        serwb_slave_phy = ClockDomainsRenamer("serwb")(serwb_slave_phy)
        self.submodules.serwb_slave_phy = serwb_slave_phy

        # Core
        serwb_slave_core = SERWBCore(serwb_slave_phy, self.clk_freq, mode="master",
            etherbone_buffer_depth = 1,
            tx_buffer_depth        = 8,
            rx_buffer_depth        = 8
        )
        serwb_slave_core = ClockDomainsRenamer("serwb")(serwb_slave_core)
        self.submodules += serwb_slave_core

        # Wishbone SRAM
        serwb_sram = ClockDomainsRenamer("serwb")(wishbone.SRAM(8192))
        self.submodules += serwb_sram
        self.comb += serwb_slave_core.bus.connect(serwb_sram.bus)

        # Analyzer ---------------------------------------------------------------------------------
        if with_analyzer:
            analyzer_signals = [
                self.serwb_master_phy.init.fsm,
                self.serwb_master_phy.serdes.rx.data,
                self.serwb_master_phy.serdes.rx.comma,
                self.serwb_master_phy.serdes.rx.idle,
                self.serwb_master_phy.serdes.tx.data,
                self.serwb_master_phy.serdes.tx.comma,
                self.serwb_master_phy.serdes.tx.idle,
                self.serwb_master_phy.serdes.rx.datapath.decoder.source,

                self.serwb_slave_phy.init.fsm,
                self.serwb_slave_phy.serdes.rx.data,
                self.serwb_slave_phy.serdes.rx.comma,
                self.serwb_slave_phy.serdes.rx.idle,
                self.serwb_slave_phy.serdes.tx.data,
                self.serwb_slave_phy.serdes.tx.comma,
                self.serwb_slave_phy.serdes.tx.idle,
                self.serwb_slave_phy.serdes.rx.datapath.decoder.source,
            ]
            self.analyzer = LiteScopeAnalyzer(analyzer_signals, 256, csr_csv="analyzer.csv")


# Build --------------------------------------------------------------------------------------------

def main():
    from litex.build.parser import LiteXArgumentParser
    parser = LiteXArgumentParser(platform=efinix_titanium_ti60_f225_dev_kit.Platform, description="LiteICLink SerWB bench on Efinix Titanium Ti60 F225 Dev Kit.")
    parser.add_target_argument("--flash",  action="store_true", help="Flash bitstream.")
    parser.add_argument("--with-analyzer", action="store_true", help="Add LiteScope Analyzer")
    args = parser.parse_args()

    platform = efinix_titanium_ti60_f225_dev_kit.Platform()
    platform.add_extension(serwb_io)

    soc     = SerWBTestSoC(platform, with_analyzer=args.with_analyzer)
    builder = Builder(soc, csr_csv="csr.csv")
    builder.build(run=args.build, **parser.toolchain_argdict)

    if args.load:
        from litex.build.openfpgaloader import OpenFPGALoader
        prog = OpenFPGALoader("titanium_ti60_f225_jtag")
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram"))

    if args.flash:
        from litex.build.openfpgaloader import OpenFPGALoader
        prog = OpenFPGALoader("titanium_ti60_f225")
        prog.flash(0, builder.get_bitstream_filename(mode="flash", ext=".hex")) # FIXME

if __name__ == "__main__":
    main()

# Use ----------------------------------------------------------------------------------------------

# ./efinix_titanium_ti60_f225_dev_kit.py --build --load
# litex_server --jtag --jtag-config=openocd_titanium_ft4232.cfg
# ./test_serwb.py --ident --init --sram --access

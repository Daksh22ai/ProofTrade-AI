const hre = require("hardhat");
const fs   = require("fs");
const path = require("path");

const OUT_PATH = path.join(__dirname, "..", "deployment.json");

async function main() {
  const network = hre.network.name;
  const [deployer] = await hre.ethers.getSigners();

  console.log(`\nNetwork: ${network}`);
  console.log(`Deployer: ${deployer.address}`);

  const balance = await hre.ethers.provider.getBalance(deployer.address);
  console.log(`Balance: ${hre.ethers.formatEther(balance)} MNT`);
  if (balance === 0n) throw new Error("Wallet has 0 balance. Fund it first.");

  // ── Reuse existing deployment if deployer matches ─────────────────────────
  // This ensures the same private key always uses the same contracts.
  // On-chain audit trail is continuous — no gaps from redeployments.
  if (fs.existsSync(OUT_PATH)) {
    const existing = JSON.parse(fs.readFileSync(OUT_PATH, "utf8"));
    if (
      existing.deployer?.toLowerCase() === deployer.address.toLowerCase() &&
      existing.network === network &&
      existing.address &&
      existing.strategy_gate_address
    ) {
      // Verify contracts still exist on-chain
      try {
        const auditCode = await hre.ethers.provider.getCode(existing.address);
        const gateCode  = await hre.ethers.provider.getCode(existing.strategy_gate_address);
        if (auditCode !== "0x" && gateCode !== "0x") {
          console.log(`\n✅ Reusing existing deployment (same deployer, same network):`);
          console.log(`   AuditLog:     ${existing.address}`);
          console.log(`   StrategyGate: ${existing.strategy_gate_address}`);
          console.log(`   Explorer:     ${existing.explorer_url}`);
          console.log(`\n   To force a fresh deployment, delete on_chain/deployment.json`);
          return;
        }
        console.log("Existing contracts not found on-chain. Deploying fresh.");
      } catch (e) {
        console.log("Could not verify existing contracts. Deploying fresh.");
      }
    }
  }

  // ── Deploy AuditLog (TradingSignalOracle v2) ──────────────────────────────
  console.log("\n[1/2] Deploying AuditLog (TradingSignalOracle)...");
  const AuditLog  = await hre.ethers.getContractFactory("AuditLog");
  const auditLog  = await AuditLog.deploy();
  await auditLog.waitForDeployment();
  const auditAddr = await auditLog.getAddress();
  const auditTx   = auditLog.deploymentTransaction()?.hash;
  const version   = await auditLog.version();
  console.log(`✅ AuditLog deployed: ${auditAddr}`);
  console.log(`   Version: ${version}`);

  // ── Deploy StrategyGate ───────────────────────────────────────────────────
  console.log("\n[2/2] Deploying StrategyGate...");
  const StrategyGate = await hre.ethers.getContractFactory("StrategyGate");
  const gate         = await StrategyGate.deploy(auditAddr);
  await gate.waitForDeployment();
  const gateAddr = await gate.getAddress();
  const gateTx   = gate.deploymentTransaction()?.hash;
  console.log(`✅ StrategyGate deployed: ${gateAddr}`);

  // ── Save deployment info ──────────────────────────────────────────────────
  const chainId = (await hre.ethers.provider.getNetwork()).chainId.toString();
  const deployment = {
    network:               network,
    chain_id:              chainId,
    address:               auditAddr,
    audit_log_address:     auditAddr,
    strategy_gate_address: gateAddr,
    deployer:              deployer.address,
    tx_hash:               auditTx,
    gate_tx_hash:          gateTx,
    explorer_url:          `https://explorer.sepolia.mantle.xyz/address/${auditAddr}`,
    gate_explorer_url:     `https://explorer.sepolia.mantle.xyz/address/${gateAddr}`,
    deployed_at_utc:       new Date().toISOString(),
    version:               version,
  };

  fs.writeFileSync(OUT_PATH, JSON.stringify(deployment, null, 2));
  console.log(`\n📄 deployment.json saved: ${OUT_PATH}`);
  console.log(`\nSummary:`);
  console.log(`  AuditLog:     ${auditAddr}`);
  console.log(`  StrategyGate: ${gateAddr}`);
  console.log(`  Explorer:     https://explorer.sepolia.mantle.xyz/address/${auditAddr}`);
  console.log(`\nVerify:`);
  console.log(`  npx hardhat verify --network mantleSepolia ${auditAddr}`);
  console.log(`  npx hardhat verify --network mantleSepolia ${gateAddr} "${auditAddr}"`);
}

main()
  .then(() => process.exit(0))
  .catch((err) => { console.error(err); process.exit(1); });

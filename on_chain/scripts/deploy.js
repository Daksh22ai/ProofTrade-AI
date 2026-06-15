const hre = require("hardhat");
const fs  = require("fs");
const path = require("path");

async function main() {
  const network = hre.network.name;
  console.log(`\nDeploying contracts to ${network}...`);

  const [deployer] = await hre.ethers.getSigners();
  console.log(`Deployer: ${deployer.address}`);

  const balance = await hre.ethers.provider.getBalance(deployer.address);
  console.log(`Balance: ${hre.ethers.formatEther(balance)} MNT`);
  if (balance === 0n) throw new Error("Wallet has 0 balance! Fund from faucet first.");

  // ── Deploy AuditLog (TradingSignalOracle v2) ──────────────────────────────
  console.log("\n[1/2] Deploying AuditLog (TradingSignalOracle)...");
  const AuditLog = await hre.ethers.getContractFactory("AuditLog");
  const auditLog = await AuditLog.deploy();
  await auditLog.waitForDeployment();
  const auditAddress = await auditLog.getAddress();
  const auditTx      = auditLog.deploymentTransaction()?.hash;
  const version      = await auditLog.version();
  console.log(`✅ AuditLog deployed: ${auditAddress}`);
  console.log(`   Version: ${version}`);

  // ── Deploy StrategyGate (composable oracle consumer) ─────────────────────
  console.log("\n[2/2] Deploying StrategyGate...");
  const StrategyGate = await hre.ethers.getContractFactory("StrategyGate");
  const gate = await StrategyGate.deploy(auditAddress);
  await gate.waitForDeployment();
  const gateAddress = await gate.getAddress();
  const gateTx      = gate.deploymentTransaction()?.hash;
  console.log(`✅ StrategyGate deployed: ${gateAddress}`);
  console.log(`   Oracle address: ${auditAddress}`);

  // ── Save deployment info ──────────────────────────────────────────────────
  const chainId = (await hre.ethers.provider.getNetwork()).chainId.toString();
  const deployment = {
    network:            network,
    chain_id:           chainId,
    address:            auditAddress,       // primary — used by submit_audit.py
    audit_log_address:  auditAddress,
    strategy_gate_address: gateAddress,
    deployer:           deployer.address,
    tx_hash:            auditTx,
    gate_tx_hash:       gateTx,
    explorer_url:       `https://explorer.sepolia.mantle.xyz/address/${auditAddress}`,
    gate_explorer_url:  `https://explorer.sepolia.mantle.xyz/address/${gateAddress}`,
    deployed_at_utc:    new Date().toISOString(),
    version:            version,
  };

  const outPath = path.join(__dirname, "..", "deployment.json");
  fs.writeFileSync(outPath, JSON.stringify(deployment, null, 2));
  console.log(`\n📄 deployment.json saved: ${outPath}`);
  console.log(`\n── Summary ────────────────────────────────────`);
  console.log(`AuditLog:     ${auditAddress}`);
  console.log(`StrategyGate: ${gateAddress}`);
  console.log(`Explorer:     https://explorer.sepolia.mantle.xyz/address/${auditAddress}`);
  console.log(`\nVerify commands:`);
  console.log(`npx hardhat verify --network mantleSepolia ${auditAddress}`);
  console.log(`npx hardhat verify --network mantleSepolia ${gateAddress} "${auditAddress}"`);
}

main()
  .then(() => process.exit(0))
  .catch((err) => { console.error(err); process.exit(1); });

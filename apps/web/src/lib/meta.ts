import type { OptConstraints, OptObjective, PreprocessConfig, UniverseId } from './types';

export interface UniverseMeta {
  id: UniverseId;
  label: string;
  bench: string;
  market: 'CN_A' | 'US_EQ';
}

export const UNIVERSES: UniverseMeta[] = [
  { id: 'CSI500', label: '中证 500', bench: '000905.SH', market: 'CN_A' },
  { id: 'CSI300', label: '沪深 300', bench: '000300.SH', market: 'CN_A' },
  { id: 'CSI1000', label: '中证 1000', bench: '000852.SH', market: 'CN_A' },
  { id: 'CN_ALL', label: '全 A（剔除 ST/次新）', bench: 'CN_ALL EW', market: 'CN_A' },
  { id: 'SP500_SAMPLE', label: 'S&P 500 样本', bench: 'SPX', market: 'US_EQ' }
];

export const universeMeta = (id: string): UniverseMeta => UNIVERSES.find((u) => u.id === id) ?? UNIVERSES[0];

export type ViewKey = 'alpha' | 'models' | 'portfolio' | 'backtest' | 'data' | 'shadow';

export const VIEWS: { key: ViewKey; label: string; path: string; prd: string; icon: ViewKey }[] = [
  { key: 'alpha', label: 'Alpha 因子工坊', path: '/alpha', prd: 'Q2', icon: 'alpha' },
  { key: 'models', label: '多因子合成·过拟合', path: '/models', prd: 'Q3', icon: 'models' },
  { key: 'portfolio', label: '组合优化·Barra', path: '/portfolio', prd: 'Q4', icon: 'portfolio' },
  { key: 'backtest', label: '回测与业绩归因', path: '/backtest', prd: 'Q5', icon: 'backtest' },
  { key: 'data', label: '数据底座·质量哨兵', path: '/data', prd: 'Q1', icon: 'data' },
  { key: 'shadow', label: '影子实盘·衰减监控', path: '/shadow', prd: 'Q6', icon: 'shadow' }
];

export const viewFromPath = (pathname: string | null): ViewKey =>
  (VIEWS.find((v) => pathname?.startsWith(v.path))?.key ?? 'alpha') as ViewKey;

/** Contract-mandated builtin factor IDs (IMPLEMENTATION-CONTRACT §2.2); names/lang are UI labels only. */
export const BUILTIN_FACTORS: { factor_id: string; name: string; lang: string }[] = [
  { factor_id: 'alpha_vp_corr_20d', name: '量价背离相关性 20D', lang: 'Polars Expr' },
  { factor_id: 'alpha_mom_resid_60d', name: '行业市值中性残差动量 60D', lang: 'Qlib DSL' },
  { factor_id: 'alpha_earn_sue_pit', name: 'PIT 业绩超预期 SUE', lang: 'Python · PIT' },
  { factor_id: 'alpha_smart_money_vwap', name: '尾盘聪明钱 VWAP 偏离', lang: 'Polars · 1min' }
];

export type PipeKey = 'winsorize_mad' | 'neutralize_industry' | 'neutralize_size' | 'orthogonalize_barra' | 'filter_limit_st';

export const PIPELINE: { key: PipeKey; label: string; short: string }[] = [
  { key: 'winsorize_mad', label: 'MAD 3.5x 去极值', short: 'MAD' },
  { key: 'neutralize_industry', label: '申万一级行业中性化', short: 'IND' },
  { key: 'neutralize_size', label: '对数流通市值中性化', short: 'SIZE' },
  { key: 'orthogonalize_barra', label: 'Barra 风格正交', short: 'BARRA' },
  { key: 'filter_limit_st', label: '剔除涨跌停/ST', short: 'ST' }
];

export const DEFAULT_PREPROCESS: PreprocessConfig = {
  winsorize_mad: true,
  mad_k: 3.5,
  neutralize_industry: true,
  neutralize_size: true,
  orthogonalize_barra: true,
  filter_limit_st: true,
  min_coverage: 0.8,
  standardize: 'zscore'
};

export const DEFAULT_CONSTRAINTS: OptConstraints = {
  objective: 'max_sharpe',
  max_stock_weight: 0.05,
  max_industry_dev: 0.03,
  turnover_lambda: 0.6,
  max_turnover_oneway: 0.35,
  target_te: 0.05,
  max_size_z: 0.5,
  max_style_z: 0.5,
  min_benchmark_weight_sum: 0.8
};

export const OBJECTIVES: { key: OptObjective; label: string }[] = [
  { key: 'max_sharpe', label: 'Max Sharpe' },
  { key: 'mean_variance', label: '均值方差' },
  { key: 'min_var', label: '最小方差' },
  { key: 'risk_parity', label: '风险平价' }
];

export const DEFAULT_EXPERIMENT_ID = 'EXP-0427';
export const DEFAULT_SHADOW_ID = 'SHADOW-CSI500-MAIN';
export const BARRA_HARD_LIMIT = 0.5;

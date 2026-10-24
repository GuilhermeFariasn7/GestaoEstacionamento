"""Calcula métricas comparando previsões do monitoramento com o gabarito manual.

Uso: python calcular_metricas_tcc.py caminho/para/previsoes.csv caminho/para/gabarito.csv
Preencha a coluna estado_real com 'ocupada' ou 'livre'. Linhas sem anotação e
previsões 'fora_de_vista' são excluídas do cálculo.
"""
import argparse
import os
import sys
import pandas as pd


def safe_div(a, b):
    return float(a / b) if b else 0.0


def metrics(frame):
    truth = frame['estado_real'].astype(str).str.strip().str.lower()
    pred = frame['previsao'].astype(str).str.strip().str.lower()
    tp = int(((truth == 'ocupada') & (pred == 'ocupada')).sum())
    tn = int(((truth == 'livre') & (pred == 'livre')).sum())
    fp = int(((truth == 'livre') & (pred == 'ocupada')).sum())
    fn = int(((truth == 'ocupada') & (pred == 'livre')).sum())
    return {
        'observacoes': int(len(frame)), 'TP': tp, 'TN': tn, 'FP': fp, 'FN': fn,
        'acuracia': safe_div(tp + tn, tp + tn + fp + fn),
        'precisao': safe_div(tp, tp + fp),
        'recall': safe_div(tp, tp + fn),
        'taxa_falsos_positivos': safe_div(fp, fp + tn),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('previsoes_csv')
    parser.add_argument('gabarito_csv')
    args = parser.parse_args()
    predictions = pd.read_csv(args.previsoes_csv, encoding='utf-8-sig')
    truth = pd.read_csv(args.gabarito_csv, encoding='utf-8-sig')
    required_pred = {'session_id', 'amostra_id', 'vaga', 'previsao'}
    required_truth = {'session_id', 'amostra_id', 'vaga', 'estado_real'}
    if not required_pred.issubset(predictions.columns):
        raise SystemExit(f'CSV de previsões precisa conter: {sorted(required_pred)}')
    if not required_truth.issubset(truth.columns):
        raise SystemExit(f'CSV de gabarito precisa conter: {sorted(required_truth)}')
    keys = ['session_id', 'amostra_id', 'vaga']
    merged = predictions.merge(truth[keys + ['estado_real']], on=keys, how='inner')
    merged['estado_real'] = merged['estado_real'].fillna('').astype(str).str.strip().str.lower()
    merged['previsao'] = merged['previsao'].fillna('').astype(str).str.strip().str.lower()
    valid = merged[merged['estado_real'].isin(['ocupada', 'livre']) & merged['previsao'].isin(['ocupada', 'livre'])].copy()
    if valid.empty:
        raise SystemExit('Nenhuma observação válida. Preencha estado_real com ocupada/livre e confira se os CSVs são da mesma coleta.')
    folder = os.path.dirname(os.path.abspath(args.gabarito_csv))
    overall = pd.DataFrame([{'vaga': 'GERAL', **metrics(valid)}])
    by_vacancy = pd.DataFrame([{'vaga': name, **metrics(group)} for name, group in valid.groupby('vaga', sort=True)])
    result = pd.concat([overall, by_vacancy], ignore_index=True)
    out = os.path.join(folder, 'metricas_tcc.csv')
    result.to_csv(out, index=False, encoding='utf-8-sig')
    print(f'Observações válidas: {len(valid)}')
    print(f'Acurácia geral: {metrics(valid)["acuracia"]:.4f}')
    print(f'Precisão geral: {metrics(valid)["precisao"]:.4f}')
    print(f'Recall geral: {metrics(valid)["recall"]:.4f}')
    print(f'Taxa de falsos positivos: {metrics(valid)["taxa_falsos_positivos"]:.4f}')
    print(f'Arquivo gerado: {out}')

if __name__ == '__main__':
    main()

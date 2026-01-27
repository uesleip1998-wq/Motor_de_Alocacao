import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import zipfile
import copy
import re
import time
import random

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Motor Alocação IFSC v23.3 (Debug)", layout="wide")
st.title("🧩 Motor de Alocação IFSC - V23.3 (Modo Detetive)")
st.markdown("""
**Lógica V23.3:**
1.  **Confiança na Planilha:** CH_Total é usada como está (ajuste manual de 80% esperado).
2.  **Logs Detalhados:** Relatório de erros explica o motivo exato de cada falha.
3.  **Time-Box:** 5 minutos.
""")

# --- CONSTANTES ---
DIAS = ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira', 'Quinta-Feira', 'Sexta-Feira']
CURSOS_SEM_SEXTA = ['EVENTOS', 'GUIA REGIONAL', 'GUIA NACIONAL']
LABS_AB = [
    "Lab. Panificação", "Lab. Confeitaria", "Lab. Habilidades", 
    "Lab. Produção", "Lab. Cozinha Regional", "Lab. Bebidas", "Lab. Panif/Conf"
]
SALAS_TEORICAS = [f"Sala {i}" for i in range(1, 13) if i != 6]
SALAS_BACKUP = ["Restaurante 1", "Lab. Informática 1", "Lab. Informática 2"]
MAX_TIME_SEC = 300

# --- FUNÇÕES AUXILIARES ---
def gerar_template():
    df = pd.DataFrame(columns=[
        "ID_Turma", "Nome_UC", "Turno", "Docentes", "Espacos", 
        "Tipo_Alocacao", "Carga_Horaria_Total", "Regra_Especial", 
        "Dia_Travado", "Semana_Inicio", "Semana_Fim"
    ])
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Demandas', index=False)
    return buffer

def converter_csv(df):
    return df.to_csv(index=False).encode('utf-8-sig')

# --- CLASSE DO MOTOR ---
class MotorAlocacao:
    def __init__(self, df_demandas, df_docentes):
        self.demandas = df_demandas.fillna("")
        self.restricoes = df_docentes.fillna("")
        self.grade_final = []
        self.erros = [] # Lista de strings simples
        self.logs_detalhados = [] # Lista de dicionários para CSV
        self.sala_base = {} 
        self.start_time = 0
        self.melhor_grade = []
        self.melhor_score = 0

    def normalizar(self, texto):
        return str(texto).strip().upper()

    def verificar_bloqueio_docente(self, docente, dia, turno, sem_ini, sem_fim):
        try:
            regra = self.restricoes[self.restricoes['Nome_Docente'] == docente]
            if not regra.empty:
                dias_indisp = str(regra.iloc[0]['Dias_Indisponiveis'])
                if dia in dias_indisp and turno in dias_indisp: return f"Indisponibilidade Docente ({dia})"
                
                if 'Bloqueio_Semana_Inicio' in regra.columns:
                    b_ini = int(regra.iloc[0]['Bloqueio_Semana_Inicio'] or 0)
                    b_fim = int(regra.iloc[0]['Bloqueio_Semana_Fim'] or 0)
                    if b_ini > 0 and b_fim > 0:
                        if not (sem_fim < b_ini or sem_ini > b_fim): return f"Bloqueio Semanal Docente ({b_ini}-{b_fim})"
        except: pass
        return None

    def otimizar_dados_entrada(self):
        df = self.demandas.copy()
        def limpar_nome(nome):
            return re.sub(r'\s*\(parte \d+\)', '', str(nome), flags=re.IGNORECASE).strip()
        df['Nome_Base'] = df['Nome_UC'].apply(limpar_nome)
        
        grupos = df.groupby(['ID_Turma', 'Nome_Base'])
        novas_demandas = []
        
        for (turma, nome), grupo in grupos:
            if len(grupo) > 1 and "PROEJA" in str(turma).upper(): 
                ch_total = grupo['Carga_Horaria_Total'].sum()
                if ch_total > 80: ch_total = 80
                docentes = ", ".join(grupo['Docentes'].unique())
                espacos = " + ".join(list(set([e.strip() for e in " + ".join(grupo['Espacos'].unique()).split('+')])))
                item = grupo.iloc[0].to_dict()
                item['Nome_UC'] = nome 
                item['Carga_Horaria_Total'] = ch_total
                item['Docentes'] = docentes
                item['Espacos'] = espacos
                item['Tipo'] = "FUSAO"
                novas_demandas.append(item)
            else:
                for _, row in grupo.iterrows():
                    row['Tipo'] = "SIMPLE"
                    novas_demandas.append(row.to_dict())
        return novas_demandas

    def definir_zoneamento(self):
        turmas_por_turno = {'Matutino': [], 'Vespertino': [], 'Noturno': []}
        turmas_unicas = self.demandas['ID_Turma'].unique()
        
        for t in turmas_unicas:
            turno = self.demandas[self.demandas['ID_Turma'] == t]['Turno'].iloc[0]
            if turno in turmas_por_turno:
                ucs = self.demandas[self.demandas['ID_Turma'] == t]
                precisa_sala = False
                for _, row in ucs.iterrows():
                    if "SEM SALA" not in str(row['Espacos']).upper() and "EAD" not in str(row['Espacos']).upper():
                        precisa_sala = True
                        break
                if precisa_sala:
                    turmas_por_turno[turno].append(t)
        
        todas_salas = SALAS_TEORICAS + SALAS_BACKUP
        for turno, lista_turmas in turmas_por_turno.items():
            for i, turma in enumerate(lista_turmas):
                if i < len(todas_salas):
                    self.sala_base[turma] = todas_salas[i]
                else:
                    self.erros.append(f"Aviso Crítico: Falta de Sala Base para {turma} no turno {turno}")
                    self.sala_base[turma] = "SEM SALA BASE"

    def preparar_demandas(self):
        lista = self.otimizar_dados_entrada()
        def peso(item):
            esp = str(item.get('Espacos', '')).upper()
            ch = float(item.get('Carga_Horaria_Total', 0))
            if "SEM SALA" in esp or "EAD" in esp: return 1
            score = 10
            if any(l.upper() in esp for l in map(str.upper, LABS_AB)): score += 100 
            score += ch 
            return -score
        
        lista.sort(key=peso)
        return lista

    def resolver_grade(self, itens_para_alocar, grade_atual):
        if time.time() - self.start_time > MAX_TIME_SEC:
            if len(grade_atual) > self.melhor_score:
                self.melhor_score = len(grade_atual)
                self.melhor_grade = copy.deepcopy(grade_atual)
            return False, []

        if not itens_para_alocar:
            return True, grade_atual
        
        if len(grade_atual) > self.melhor_score:
            self.melhor_score = len(grade_atual)
            self.melhor_grade = copy.deepcopy(grade_atual)

        item = itens_para_alocar[0]
        restante = itens_para_alocar[1:]
        
        espacos_str = str(item.get('Espacos', '')).upper()
        if "EAD" in espacos_str or "100% EAD" in str(item.get('Regra_Especial', '')).upper():
            nova_grade = copy.deepcopy(grade_atual)
            nova_grade.append(item | {"Alocacao": {"dia": "EAD", "sala": "EAD", "sem_ini": 1, "sem_fim": 20, "status": "✅ Alocado (EAD)", "is_ead": True}})
            return self.resolver_grade(restante, nova_grade)

        eh_sem_sala = "SEM SALA" in espacos_str
        recursos_necessarios = []
        sala_visual = ""
        
        if not eh_sem_sala:
            for lab in LABS_AB:
                if lab.upper() in espacos_str: recursos_necessarios.append(lab)
            sala_b = self.sala_base.get(item['ID_Turma'])
            if sala_b: recursos_necessarios.append(sala_b)
            sala_visual = " + ".join(recursos_necessarios)
        else:
            sala_visual = "Virtual/Sem Sala"

        # CH Pura da Planilha
        ch_total = float(item['Carga_Horaria_Total'] or 0)
        duracao_semanas = int(np.ceil(ch_total / 4))
        
        movimentos = []
        dias_teste = DIAS
        if item.get('Dia_Travado'): dias_teste = [item['Dia_Travado']]
        eh_curso_sem_sexta = any(c in str(item['ID_Turma']).upper() for c in CURSOS_SEM_SEXTA)

        inicios_estrategicos = [1, 11, 6, 16] 
        
        for dia in dias_teste:
            if dia == 'Sexta-Feira' and eh_curso_sem_sexta: continue
            for ini in inicios_estrategicos:
                if ini > 22 - duracao_semanas + 1: continue
                fim = ini + duracao_semanas - 1
                movimentos.append({
                    "tipo": "BLOCO", "dia": dia, "sem_ini": ini, "sem_fim": fim, 
                    "recursos": recursos_necessarios, "ch": ch_total
                })

        if ch_total >= 40:
            metade = int(duracao_semanas / 2)
            for ini in [1, 11]:
                fim = ini + metade - 1
                for d1 in dias_teste:
                    if d1 == 'Sexta-Feira' and eh_curso_sem_sexta: continue
                    for d2 in dias_teste:
                        if d1 == d2: continue
                        if d2 == 'Sexta-Feira' and eh_curso_sem_sexta: continue
                        movimentos.append({
                            "tipo": "SPLIT", "dias": [d1, d2], "sem_ini": ini, "sem_fim": fim,
                            "recursos": recursos_necessarios, "ch": ch_total
                        })
        
        tentativas_falhas = []
        
        for mov in movimentos:
            valido, motivo = self.movimento_valido(mov, item, grade_atual)
            if valido:
                nova_grade = copy.deepcopy(grade_atual)
                status_str = "✅ Alocado"
                if mov['tipo'] == "SPLIT": status_str += " (Split)"
                if eh_sem_sala: status_str += " (Sem Sala)"

                nova_grade.append(item | {
                    "Alocacao": {
                        "dia": mov['dia'] if mov['tipo'] == "BLOCO" else f"{mov['dias'][0]} e {mov['dias'][1]}",
                        "sala": sala_visual,
                        "sem_ini": mov['sem_ini'],
                        "sem_fim": mov['sem_fim'],
                        "status": status_str,
                        "config": mov
                    }
                })
                
                sucesso, grade_final = self.resolver_grade(restante, nova_grade)
                if sucesso: return True, grade_final
            else:
                # Registra o motivo da falha para este movimento específico
                desc_mov = f"{mov['dia']} (Sem {mov['sem_ini']}-{mov['sem_fim']})" if mov['tipo'] == 'BLOCO' else f"Split {mov['dias']}"
                tentativas_falhas.append(f"{desc_mov}: {motivo}")
        
        # Se chegou aqui, falhou em todos os movimentos
        self.erros.append(f"Falha: {item['ID_Turma']} - {item['Nome_UC']}")
        self.logs_detalhados.append({
            "ID_Turma": item['ID_Turma'],
            "UC": item['Nome_UC'],
            "Tentativas": len(movimentos),
            "Detalhes_Falha": " | ".join(tentativas_falhas[:5]) + "..." # Limita tamanho
        })
        
        return self.resolver_grade(restante, grade_atual)

    def movimento_valido(self, mov, item, grade):
        docs_item = [d.strip() for d in str(item['Docentes']).split(',')]
        turma_item = str(item['ID_Turma'])
        turno_item = item['Turno']

        slots_teste = []
        if mov['tipo'] == "BLOCO":
            slots_teste.append((mov['dia'], mov['sem_ini'], mov['sem_fim']))
        else:
            slots_teste.append((mov['dias'][0], mov['sem_ini'], mov['sem_fim']))
            slots_teste.append((mov['dias'][1], mov['sem_ini'], mov['sem_fim']))

        # Verifica Bloqueio Docente (Prioritário)
        for d_t, ini_t, fim_t in slots_teste:
            for d in docs_item:
                motivo_doc = self.verificar_bloqueio_docente(d, d_t, turno_item, ini_t, fim_t)
                if motivo_doc: return False, motivo_doc

        for alocada in grade:
            if alocada['Alocacao'].get('is_ead'): continue
            
            cfg = alocada['Alocacao']['config']
            turno_aloc = alocada['Turno']
            if turno_item != turno_aloc: continue 

            slots_aloc = []
            if cfg['tipo'] == "BLOCO":
                slots_aloc.append((cfg['dia'], cfg['sem_ini'], cfg['sem_fim']))
            else:
                slots_aloc.append((cfg['dias'][0], cfg['sem_ini'], cfg['sem_fim']))
                slots_aloc.append((cfg['dias'][1], cfg['sem_ini'], cfg['sem_fim']))

            for d_t, ini_t, fim_t in slots_teste:
                for d_a, ini_a, fim_a in slots_aloc:
                    if d_t == d_a:
                        if not (fim_t < ini_a or ini_t > fim_a):
                            # Colisão Temporal
                            if turma_item == str(alocada['ID_Turma']): 
                                return False, f"Turma Ocupada com {alocada['UC']}"
                            
                            docs_aloc = [d.strip() for d in str(alocada['Docentes']).split(',')]
                            if any(d in docs_aloc for d in docs_item): 
                                return False, f"Docente Ocupado em {alocada['ID_Turma']}"
                            
                            rec_t = mov.get('recursos', [])
                            rec_a = cfg.get('recursos', [])
                            if rec_t and rec_a:
                                if any(r in rec_a for r in rec_t): 
                                    return False, f"Sala/Lab Ocupado por {alocada['ID_Turma']}"

        return True, "OK"

    def executar(self):
        self.start_time = time.time()
        self.definir_zoneamento()
        fila = self.preparar_demandas()
        
        msg_area = st.empty()
        msg_area.info("Iniciando alocação V23.3...")
        
        sucesso, grade_resolvida = self.resolver_grade(fila, [])
        
        if not grade_resolvida and self.melhor_grade:
            grade_resolvida = self.melhor_grade

        res = []
        for item in grade_resolvida:
            alo = item['Alocacao']
            res.append({
                "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "CH_Total": item['Carga_Horaria_Total'],
                "Dia": alo['dia'], "Turno": item['Turno'], "Docentes": item['Docentes'],
                "Espacos": alo['sala'], "Semana_Inicio": alo['sem_ini'], "Semana_Fim": alo['sem_fim'],
                "Status": alo['status']
            })
            
        alocados_ids = [f"{i['ID_Turma']}-{i['UC']}" for i in res]
        for item in fila:
            uid = f"{item['ID_Turma']}-{item['Nome_UC']}"
            if uid not in alocados_ids:
                res.append({
                    "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "CH_Total": item['Carga_Horaria_Total'],
                    "Status": "❌ Não Alocado"
                })

        return pd.DataFrame(res), self.logs_detalhados

# --- INTERFACE ---
st.sidebar.header("📂 Área de Trabalho")
st.sidebar.download_button("📥 Baixar Modelo", gerar_template(), "modelo.xlsx")
st.sidebar.markdown("---")
up = st.sidebar.file_uploader("Upload Planilha", type=['xlsx'])

if up and st.button("🚀 Rodar Otimizador V23.3"):
    try:
        df_dem = pd.read_excel(up, sheet_name='Demandas')
        try: df_doc = pd.read_excel(up, sheet_name='Docentes')
        except: df_doc = pd.DataFrame()
        
        motor = MotorAlocacao(df_dem, df_doc)
        df_res, logs = motor.executar()
        
        st.success("Processamento Finalizado!")
        
        if logs:
            st.warning(f"{len(logs)} itens não foram alocados.")
            df_logs = pd.DataFrame(logs)
            st.dataframe(df_logs)

        buf = BytesIO()
        with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED, False) as z:
            z.writestr("01_Grade_Geral.csv", converter_csv(df_res))
            z.writestr("02_Relatorio_Erros_Detalhados.csv", converter_csv(pd.DataFrame(logs)))
            z.writestr("05_Dados_Brutos.json", df_res.to_json(orient='records', indent=4))
        
        st.download_button("📦 Baixar Resultados (ZIP)", buf.getvalue(), "Resultados_V23.3.zip", "application/zip")
        st.dataframe(df_res)
        
    except Exception as e:
        st.error(f"Erro Crítico: {e}")

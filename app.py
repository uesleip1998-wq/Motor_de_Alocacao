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
st.set_page_config(page_title="Motor Alocação IFSC v23.6 (Shuffle Solver)", layout="wide")
st.title("🧩 Motor de Alocação IFSC - V23.6 (Encaixe Perfeito)")
st.markdown("""
**Lógica V23.6:**
1.  **Restrição Rígida:** Sexta-feira bloqueada para Guia/Eventos (320h máx).
2.  **Motor de Persistência:** Se a alocação falhar, o sistema reordena as disciplinas e tenta de novo (várias vezes) para achar o encaixe perfeito.
3.  **Foco em Saturação:** Turmas cheias têm prioridade total de processamento.
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
        self.erros = [] 
        self.logs_detalhados = [] 
        self.sala_base = {} 
        self.start_time = 0
        self.saturacao_turmas = {}

    def normalizar(self, texto):
        return str(texto).strip().upper()

    def verificar_bloqueio_docente(self, docente, dia, turno, sem_ini, sem_fim):
        try:
            regra = self.restricoes[self.restricoes['Nome_Docente'] == docente]
            if not regra.empty:
                dias_indisp = str(regra.iloc[0]['Dias_Indisponiveis'])
                if dia in dias_indisp and turno in dias_indisp: return True
                
                if 'Bloqueio_Semana_Inicio' in regra.columns:
                    b_ini = int(regra.iloc[0]['Bloqueio_Semana_Inicio'] or 0)
                    b_fim = int(regra.iloc[0]['Bloqueio_Semana_Fim'] or 0)
                    if b_ini > 0 and b_fim > 0:
                        if not (sem_fim < b_ini or sem_ini > b_fim): return True
        except: pass
        return False

    def otimizar_dados_entrada(self):
        df = self.demandas.copy()
        def limpar_nome(nome):
            return re.sub(r'\s*\(parte \d+\)', '', str(nome), flags=re.IGNORECASE).strip()
        df['Nome_Base'] = df['Nome_UC'].apply(limpar_nome)
        
        for turma in df['ID_Turma'].unique():
            total = df[df['ID_Turma'] == turma]['Carga_Horaria_Total'].sum()
            self.saturacao_turmas[turma] = total

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
        # Retorna dicionário {Turma: [Lista de UCs]} para processamento isolado
        lista = self.otimizar_dados_entrada()
        demandas_por_turma = {}
        
        # Ordenação Global Inicial (Apenas para definir quem processa primeiro)
        # Turmas mais cheias primeiro
        def peso_turma(t_id):
            saturacao = self.saturacao_turmas.get(t_id, 0)
            eh_sem_sexta = any(c in str(t_id).upper() for c in CURSOS_SEM_SEXTA)
            if eh_sem_sexta and saturacao >= 300: return -5000
            if saturacao >= 380: return -5000
            return -saturacao

        turmas_ordenadas = sorted(list(set([i['ID_Turma'] for i in lista])), key=peso_turma)
        
        for item in lista:
            t = item['ID_Turma']
            if t not in demandas_por_turma: demandas_por_turma[t] = []
            demandas_por_turma[t].append(item)
            
        return turmas_ordenadas, demandas_por_turma

    def tentar_alocar_turma(self, ucs_da_turma, grade_global):
        # Tenta alocar todas as UCs de uma turma específica
        # Retorna (Sucesso, Grade_Atualizada_Com_A_Turma)
        
        grade_local = copy.deepcopy(grade_global)
        ucs_pendentes = ucs_da_turma.copy()
        
        # Ordenação interna padrão: Labs > Maiores > Menores
        ucs_pendentes.sort(key=lambda x: (
            -1000 if any(l.upper() in str(x.get('Espacos','')).upper() for l in map(str.upper, LABS_AB)) else 0,
            -float(x.get('Carga_Horaria_Total', 0))
        ))

        for item in ucs_pendentes:
            sucesso, nova_grade = self.alocar_item_individual(item, grade_local)
            if sucesso:
                grade_local = nova_grade
            else:
                return False, grade_global # Falhou a turma inteira nesta tentativa
        
        return True, grade_local

    def alocar_item_individual(self, item, grade):
        # Lógica de alocação de um único item (sem recursão profunda, apenas busca linear)
        espacos_str = str(item.get('Espacos', '')).upper()
        if "EAD" in espacos_str or "100% EAD" in str(item.get('Regra_Especial', '')).upper():
            grade.append(item | {"Alocacao": {"dia": "EAD", "sala": "EAD", "sem_ini": 1, "sem_fim": 20, "status": "✅ Alocado (EAD)", "is_ead": True}})
            return True, grade

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

        ch_total = float(item['Carga_Horaria_Total'] or 0)
        duracao_semanas = int(np.ceil(ch_total / 4))
        
        dias_teste = DIAS
        if item.get('Dia_Travado'): dias_teste = [item['Dia_Travado']]
        eh_curso_sem_sexta = any(c in str(item['ID_Turma']).upper() for c in CURSOS_SEM_SEXTA)

        # Gera movimentos possíveis
        movimentos = []
        inicios = [1, 11, 6, 16] 
        if ch_total <= 20: inicios = list(range(1, 22 - duracao_semanas + 1)) # Flexibilidade total para pequenos

        for dia in dias_teste:
            if dia == 'Sexta-Feira' and eh_curso_sem_sexta: continue
            for ini in inicios:
                if ini > 22 - duracao_semanas + 1: continue
                fim = ini + duracao_semanas - 1
                movimentos.append({"tipo": "BLOCO", "dia": dia, "sem_ini": ini, "sem_fim": fim})

        if ch_total >= 40:
            metade = int(duracao_semanas / 2)
            for ini in [1, 11]:
                fim = ini + metade - 1
                for d1 in dias_teste:
                    if d1 == 'Sexta-Feira' and eh_curso_sem_sexta: continue
                    for d2 in dias_teste:
                        if d1 == d2: continue
                        if d2 == 'Sexta-Feira' and eh_curso_sem_sexta: continue
                        movimentos.append({"tipo": "SPLIT", "dias": [d1, d2], "sem_ini": ini, "sem_fim": fim})
        
        # Tenta encontrar um movimento válido
        for mov in movimentos:
            if self.movimento_valido(mov, item, grade):
                status_str = "✅ Alocado"
                if mov['tipo'] == "SPLIT": status_str += " (Split)"
                if eh_sem_sala: status_str += " (Sem Sala)"
                
                grade.append(item | {
                    "Alocacao": {
                        "dia": mov['dia'] if mov['tipo'] == "BLOCO" else f"{mov['dias'][0]} e {mov['dias'][1]}",
                        "sala": sala_visual,
                        "sem_ini": mov['sem_ini'],
                        "sem_fim": mov['sem_fim'],
                        "status": status_str,
                        "config": mov
                    }
                })
                return True, grade
        
        return False, grade

    def movimento_valido(self, mov, item, grade):
        docs_item = [d.strip() for d in str(item['Docentes']).split(',')]
        turma_item = str(item['ID_Turma'])
        turno_item = item['Turno']
        
        # Verifica Bloqueio Docente
        slots_teste = []
        if mov['tipo'] == "BLOCO":
            slots_teste.append((mov['dia'], mov['sem_ini'], mov['sem_fim']))
        else:
            slots_teste.append((mov['dias'][0], mov['sem_ini'], mov['sem_fim']))
            slots_teste.append((mov['dias'][1], mov['sem_ini'], mov['sem_fim']))

        for d_t, ini_t, fim_t in slots_teste:
            for d in docs_item:
                if self.verificar_bloqueio_docente(d, d_t, turno_item, ini_t, fim_t): return False

        # Verifica Conflitos com Grade
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
                            if turma_item == str(alocada['ID_Turma']): return False
                            docs_aloc = [d.strip() for d in str(alocada['Docentes']).split(',')]
                            if any(d in docs_aloc for d in docs_item): return False
                            
                            # Verifica Sala/Lab (Recursos Físicos)
                            rec_t = []
                            esp_t = str(item.get('Espacos', '')).upper()
                            for lab in LABS_AB: 
                                if lab.upper() in esp_t: rec_t.append(lab)
                            sb_t = self.sala_base.get(item['ID_Turma'])
                            if sb_t and "SEM SALA" not in esp_t: rec_t.append(sb_t)

                            rec_a = []
                            esp_a = str(alocada.get('Espacos', '')).upper()
                            for lab in LABS_AB:
                                if lab.upper() in esp_a: rec_a.append(lab)
                            sb_a = self.sala_base.get(alocada['ID_Turma'])
                            if sb_a and "SEM SALA" not in esp_a: rec_a.append(sb_a)

                            if rec_t and rec_a:
                                if any(r in rec_a for r in rec_t): return False
        return True

    def executar(self):
        self.start_time = time.time()
        self.definir_zoneamento()
        turmas_ord, demandas = self.preparar_demandas()
        
        msg_area = st.empty()
        grade_global = []
        
        total_turmas = len(turmas_ord)
        
        for idx, t_id in enumerate(turmas_ord):
            ucs = demandas[t_id]
            msg_area.info(f"Processando Turma {idx+1}/{total_turmas}: {t_id} (Tentando encaixe perfeito...)")
            
            # Tenta alocar a turma. Se falhar, embaralha e tenta de novo (até 50x)
            sucesso_turma = False
            melhor_resultado_turma = []
            max_alocados = -1
            
            # 1. Tentativa Padrão (Ordenada)
            sucesso, grade_temp = self.tentar_alocar_turma(ucs, grade_global)
            if sucesso:
                grade_global = grade_temp
                sucesso_turma = True
            else:
                # 2. Modo Shuffle (Persistência)
                # Se a turma está saturada (perto de 100%), tentamos várias permutações
                tentativas = 50 if self.saturacao_turmas.get(t_id, 0) >= 300 else 5
                
                ucs_shuffle = ucs.copy()
                for _ in range(tentativas):
                    if time.time() - self.start_time > MAX_TIME_SEC: break
                    
                    random.shuffle(ucs_shuffle)
                    suc, g_temp = self.tentar_alocar_turma(ucs_shuffle, grade_global)
                    
                    # Conta quantos foram alocados nessa tentativa
                    # (A função retorna False se UM falhar, mas queremos saber qual foi o "menos pior")
                    # Na verdade, minha função 'tentar_alocar_turma' retorna a grade global antiga se falhar.
                    # Precisamos de uma função que retorne o parcial.
                    # Simplificação: Se falhar o shuffle, não atualiza a global.
                    
                    if suc:
                        grade_global = g_temp
                        sucesso_turma = True
                        break
            
            if not sucesso_turma:
                # Se falhou tudo, aloca o que der (Modo Guloso Final)
                # Isso vai gerar erro no relatório, mas garante o parcial
                for item in ucs:
                    suc_item, g_item = self.alocar_item_individual(item, grade_global)
                    if suc_item:
                        grade_global = g_item
                    else:
                        self.erros.append(f"Falha Irrecuperável: {item['ID_Turma']} - {item['Nome_UC']}")

        # Formata Saída
        res = []
        alocados_ids = []
        for item in grade_global:
            alo = item['Alocacao']
            uid = f"{item['ID_Turma']}-{item['Nome_UC']}"
            alocados_ids.append(uid)
            res.append({
                "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "CH_Total": item['Carga_Horaria_Total'],
                "Dia": alo['dia'], "Turno": item['Turno'], "Docentes": item['Docentes'],
                "Espacos": alo['sala'], "Semana_Inicio": alo['sem_ini'], "Semana_Fim": alo['sem_fim'],
                "Status": alo['status']
            })
            
        # Verifica o que faltou
        for t_id in turmas_ord:
            for item in demandas[t_id]:
                uid = f"{item['ID_Turma']}-{item['Nome_UC']}"
                if uid not in alocados_ids:
                    res.append({
                        "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "CH_Total": item['Carga_Horaria_Total'],
                        "Status": "❌ Não Alocado (Sem espaço físico/temporal)"
                    })

        return pd.DataFrame(res), self.erros

# --- INTERFACE ---
st.sidebar.header("📂 Área de Trabalho")
st.sidebar.download_button("📥 Baixar Modelo", gerar_template(), "modelo.xlsx")
st.sidebar.markdown("---")
up = st.sidebar.file_uploader("Upload Planilha", type=['xlsx'])

if up and st.button("🚀 Rodar Otimizador V23.6"):
    try:
        df_dem = pd.read_excel(up, sheet_name='Demandas')
        try: df_doc = pd.read_excel(up, sheet_name='Docentes')
        except: df_doc = pd.DataFrame()
        
        motor = MotorAlocacao(df_dem, df_doc)
        df_res, logs = motor.executar()
        
        st.success("Processamento Finalizado!")
        
        if logs:
            st.warning(f"{len(logs)} itens não foram alocados.")
            st.write(logs)

        buf = BytesIO()
        with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED, False) as z:
            z.writestr("01_Grade_Geral.csv", converter_csv(df_res))
            z.writestr("05_Dados_Brutos.json", df_res.to_json(orient='records', indent=4))
        
        st.download_button("📦 Baixar Resultados (ZIP)", buf.getvalue(), "Resultados_V23.6.zip", "application/zip")
        st.dataframe(df_res)
        
    except Exception as e:
        st.error(f"Erro Crítico: {e}")

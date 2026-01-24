import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import zipfile

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Motor Alocação IFSC v14.0", layout="wide")
st.title("🧩 Motor de Alocação IFSC - Matriz Binária (V14)")
st.markdown("""
**Arquitetura V14:**
1.  **Matriz de Ocupação:** Garante integridade matemática (zero sobreposição).
2.  **Ondas de Execução:** Prioriza FIC e Licenças > Blocos Grandes > Encaixes Pequenos.
3.  **Split Automático:** Divide UCs de 40h em 2 dias se não houver janela única.
""")

# --- CONSTANTES ---
DIAS = ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira', 'Quinta-Feira', 'Sexta-Feira']
TURNOS = ['Matutino', 'Vespertino', 'Noturno']
SEMANAS = range(1, 23) # 22 Semanas Letivas

LABS_AB = [
    "Lab. Panificação", "Lab. Confeitaria", "Lab. Habilidades", 
    "Lab. Produção", "Lab. Cozinha Regional", "Lab. Bebidas", "Lab. Panif/Conf"
]
SALAS_TEORICAS = [f"Sala {i}" for i in range(1, 13) if i != 6]
SALAS_BACKUP = ["Lab. Informática 1", "Lab. Informática 2", "Restaurante 1"]

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
        self.grade = []
        self.erros = []
        
        # MATRIZ DE OCUPAÇÃO (Dicionário Otimizado)
        # Chave: "RECURSO|DIA|TURNO|SEMANA" -> Valor: True (Ocupado)
        self.matriz = {}

    def normalizar(self, texto):
        """Padroniza strings para evitar erros de digitação (Sala 1 != sala 1)"""
        return str(texto).strip().upper()

    def verificar_conflito(self, recursos, dia, turno, sem_ini, sem_fim):
        """Retorna lista de recursos em conflito"""
        conflitos = []
        for rec in recursos:
            rec_norm = self.normalizar(rec)
            for sem in range(sem_ini, sem_fim + 1):
                chave = f"{rec_norm}|{dia}|{turno}|{sem}"
                if self.matriz.get(chave):
                    conflitos.append(rec)
                    break # Se falhou uma semana, o recurso já era
        return list(set(conflitos))

    def reservar(self, recursos, dia, turno, sem_ini, sem_fim):
        """Grava na matriz"""
        for rec in recursos:
            rec_norm = self.normalizar(rec)
            for sem in range(sem_ini, sem_fim + 1):
                chave = f"{rec_norm}|{dia}|{turno}|{sem}"
                self.matriz[chave] = True

    def buscar_sala(self, dia, turno, sem_ini, sem_fim):
        # 1. Teóricas
        for sala in SALAS_TEORICAS:
            if not self.verificar_conflito([sala], dia, turno, sem_ini, sem_fim):
                return sala
        # 2. Backups
        for sala in SALAS_BACKUP:
            if not self.verificar_conflito([sala], dia, turno, sem_ini, sem_fim):
                return sala
        return "PENDENTE"

    def verificar_bloqueio_docente(self, docente, dia, turno, sem_ini, sem_fim):
        # Verifica Dia Fixo
        try:
            regra = self.restricoes[self.restricoes['Nome_Docente'] == docente]
            if not regra.empty:
                dias_indisp = str(regra.iloc[0]['Dias_Indisponiveis'])
                if dia in dias_indisp and turno in dias_indisp: return True
                
                # Verifica Licença Temporal (Simulação: Se tiver 'licença' nas obs)
                obs = str(regra.iloc[0]['Restricoes_Extras']).lower()
                if "licença" in obs:
                    # Lógica simplificada: Se tem licença, bloqueia semanas 16-22 (exemplo)
                    # Ideal: Ler colunas Semana_Ini/Fim da planilha Docentes
                    if sem_fim > 15: return True
        except: pass
        return False

    def processar_ondas(self):
        # PREPARAÇÃO DOS DADOS
        lista_demandas = []
        
        # Agrupamento (Pareamento 60+20)
        turmas = self.demandas['ID_Turma'].unique()
        for turma in turmas:
            df_t = self.demandas[self.demandas['ID_Turma'] == turma].copy()
            ucs_60 = df_t[df_t['Carga_Horaria_Total'] == 60].to_dict('records')
            ucs_20 = df_t[df_t['Carga_Horaria_Total'] == 20].to_dict('records')
            outras = df_t[~df_t['Carga_Horaria_Total'].isin([20, 60])].to_dict('records')
            
            # Pareamento
            while ucs_60 and ucs_20:
                u60 = ucs_60.pop(0)
                u20 = ucs_20.pop(0)
                item = {
                    "Tipo": "PAREO", "Componentes": [u60, u20],
                    "ID_Turma": turma, "Nome_UC": f"{u60['Nome_UC']} + {u20['Nome_UC']}",
                    "Turno": u60['Turno'], "Dia_Travado": u60['Dia_Travado'] or u20['Dia_Travado'],
                    "Semana_Inicio": u60['Semana_Inicio'], "Regra_Especial": u60['Regra_Especial']
                }
                lista_demandas.append(item)
            
            for u in ucs_60 + ucs_20 + outras:
                u['Tipo'] = "SIMPLE"
                lista_demandas.append(u)

        # DEFINIÇÃO DE ONDAS
        # Onda 1: FIC e Dia Travado (Rígidos)
        # Onda 2: Pareados e CH >= 60 (Grandes)
        # Onda 3: CH <= 40 (Pequenos/Encaixes)
        def get_onda(item):
            if "FIC" in str(item['ID_Turma']): return 1
            if item['Dia_Travado']: return 1
            if item['Tipo'] == "PAREO": return 2
            ch = float(item.get('Carga_Horaria_Total', 0) or 0)
            if ch >= 60: return 2
            return 3
            
        lista_demandas.sort(key=get_onda)
        
        # EXECUÇÃO
        total = len(lista_demandas)
        bar = st.progress(0)
        
        for idx, item in enumerate(lista_demandas):
            sucesso = False
            
            # --- SETUP ---
            if item['Tipo'] == "PAREO":
                u1, u2 = item['Componentes'][0], item['Componentes'][1]
                ch_total = 80
                # Testa ordem normal e invertida
                ordens = [[(u1, 15), (u2, 5)], [(u2, 5), (u1, 15)]]
            else:
                ch_total = float(item['Carga_Horaria_Total'] or 0)
                dur_ideal = int(np.ceil(ch_total / 4))
                # Regra 80/20
                if "80%" in str(item['Regra_Especial']):
                    meta_presencial = ch_total * 0.8
                    dur_ideal = int(np.ceil(meta_presencial / 4))
                else:
                    meta_presencial = ch_total

            # Dias
            dias_tentativa = DIAS
            if item['Dia_Travado']:
                dias_tentativa = [item['Dia_Travado']]
                if "FIC" not in str(item['ID_Turma']):
                    dias_tentativa += [d for d in DIAS if d != item['Dia_Travado']]

            # --- TENTATIVA DE ALOCAÇÃO ---
            for dia in dias_tentativa:
                if sucesso: break
                
                # Pontos de Partida
                starts = [1]
                if item['Tipo'] != "PAREO" and dur_ideal <= 11: starts.append(11)
                if item['Semana_Inicio']: starts = [int(item['Semana_Inicio'])]
                if "FIC" in str(item['ID_Turma']): starts = [4]

                for inicio in starts:
                    if sucesso: break
                    inicio_real = 2 if (inicio == 1 and dia in ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira']) else inicio

                    # Sliding Window
                    for shift in range(15):
                        
                        # LOGICA PAREO
                        if item['Tipo'] == "PAREO":
                            for config in ordens: # Testa ordem normal e invertida
                                p1, dur1 = config[0]
                                p2, dur2 = config[1]
                                
                                s1_ini, s1_fim = inicio_real + shift, inicio_real + shift + dur1 - 1
                                s2_ini, s2_fim = s1_fim + 1, s1_fim + dur2
                                if s2_fim > 22: continue

                                # Verifica P1
                                docs1 = [d.strip() for d in str(p1['Docentes']).split(',')]
                                if any(self.verificar_bloqueio_docente(d, dia, item['Turno'], s1_ini, s1_fim) for d in docs1): continue
                                
                                esp1 = str(p1['Espacos'])
                                sala1 = ""
                                if "Sala Teórica" in esp1: sala1 = self.buscar_sala(dia, item['Turno'], s1_ini, s1_fim)
                                elif any(l in esp1 for l in LABS_AB) and s1_ini < 4: sala1 = self.buscar_sala(dia, item['Turno'], s1_ini, min(3, s1_fim))
                                
                                rec1 = docs1 + [str(item['ID_Turma'])] + ([sala1] if sala1 and sala1 != "PENDENTE" else [])
                                # Adiciona Labs se necessário
                                if any(l in esp1 for l in LABS_AB) and s1_fim >= 4:
                                    rec1 += [e.strip() for e in esp1.split('+') if e.strip() in LABS_AB]
                                
                                if self.verificar_conflito(rec1, dia, item['Turno'], s1_ini, s1_fim): continue

                                # Verifica P2 (Similar a P1)
                                docs2 = [d.strip() for d in str(p2['Docentes']).split(',')]
                                if any(self.verificar_bloqueio_docente(d, dia, item['Turno'], s2_ini, s2_fim) for d in docs2): continue
                                esp2 = str(p2['Espacos'])
                                sala2 = ""
                                if "Sala Teórica" in esp2: sala2 = self.buscar_sala(dia, item['Turno'], s2_ini, s2_fim)
                                
                                rec2 = docs2 + [str(item['ID_Turma'])] + ([sala2] if sala2 and sala2 != "PENDENTE" else [])
                                if any(l in esp2 for l in LABS_AB) and s2_fim >= 4:
                                    rec2 += [e.strip() for e in esp2.split('+') if e.strip() in LABS_AB]

                                if self.verificar_conflito(rec2, dia, item['Turno'], s2_ini, s2_fim): continue

                                # SUCESSO PAREO
                                self.reservar(rec1, dia, item['Turno'], s1_ini, s1_fim)
                                self.reservar(rec2, dia, item['Turno'], s2_ini, s2_fim)
                                
                                self.grade.append({
                                    "ID_Turma": item['ID_Turma'], "UC": p1['Nome_UC'], "CH_Total": p1['Carga_Horaria_Total'],
                                    "Dia": dia, "Turno": item['Turno'], "Docentes": p1['Docentes'],
                                    "Espacos": f"{esp1} ({sala1})", "Semana_Inicio": s1_ini, "Semana_Fim": s1_fim,
                                    "Status": "✅ Alocado (Pareado)"
                                })
                                self.grade.append({
                                    "ID_Turma": item['ID_Turma'], "UC": p2['Nome_UC'], "CH_Total": p2['Carga_Horaria_Total'],
                                    "Dia": dia, "Turno": item['Turno'], "Docentes": p2['Docentes'],
                                    "Espacos": f"{esp2} ({sala2})", "Semana_Inicio": s2_ini, "Semana_Fim": s2_fim,
                                    "Status": "✅ Alocado (Pareado)"
                                })
                                sucesso = True
                                break
                        
                        # LOGICA SIMPLE
                        else:
                            sem_ini = inicio_real + shift
                            sem_fim = sem_ini + dur_ideal - 1
                            if sem_fim > 22: sem_fim = 22
                            dur_real = sem_fim - sem_ini + 1
                            if dur_real <= 0: break

                            docs = [d.strip() for d in str(item['Docentes']).split(',')]
                            if any(self.verificar_bloqueio_docente(d, dia, item['Turno'], sem_ini, sem_fim) for d in docs): continue

                            esp = str(item['Espacos'])
                            sala = ""
                            rec = docs + [str(item['ID_Turma'])]
                            
                            # Lógica Sala
                            if "Sala Teórica" in esp:
                                sala = self.buscar_sala(dia, item['Turno'], sem_ini, sem_fim)
                                if sala != "PENDENTE": rec.append(sala)
                            elif any(l in esp for l in LABS_AB):
                                # Lab precisa de sala no inicio?
                                if sem_ini < 4:
                                    sala = self.buscar_sala(dia, item['Turno'], sem_ini, min(3, sem_fim))
                                    if sala != "PENDENTE": rec.append(sala)
                                # Adiciona Lab nas semanas certas
                                if sem_fim >= 4:
                                    rec += [e.strip() for e in esp.split('+') if e.strip() in LABS_AB]

                            if not self.verificar_conflito(rec, dia, item['Turno'], sem_ini, sem_fim):
                                self.reservar(rec, dia, item['Turno'], sem_ini, sem_fim)
                                
                                ch_aloc = dur_real * 4
                                status = "✅ Alocado"
                                if ch_aloc < meta_presencial: status = "⚠️ Parcial"
                                elif ch_aloc < ch_total: status = "✅ Alocado (Híbrido)"
                                
                                self.grade.append({
                                    "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "CH_Total": ch_total,
                                    "Dia": dia, "Turno": item['Turno'], "Docentes": item['Docentes'],
                                    "Espacos": f"{esp} ({sala})", "Semana_Inicio": sem_ini, "Semana_Fim": sem_fim,
                                    "Status": status, "Obs": f"{ch_aloc}h Presenciais"
                                })
                                sucesso = True
                                break
                            
                            # TENTATIVA SPLIT (Se falhou normal e é UC de 40h)
                            elif ch_total == 40 and not sucesso:
                                # Procura 2 dias com 5 semanas cada (20h + 20h = 40h)
                                # Simplificação V14: Tenta achar outro dia livre no mesmo turno
                                for dia2 in [d for d in DIAS if d != dia]:
                                    # Verifica se dia2 está livre para SEM 1-5
                                    s_split_ini, s_split_fim = sem_ini, sem_ini + 4
                                    if s_split_fim > 22: continue
                                    
                                    # Verifica Dia 1 (dia)
                                    if self.verificar_conflito(rec, dia, item['Turno'], s_split_ini, s_split_fim): continue
                                    # Verifica Dia 2 (dia2)
                                    if self.verificar_conflito(rec, dia2, item['Turno'], s_split_ini, s_split_fim): continue
                                    
                                    # SUCESSO SPLIT
                                    self.reservar(rec, dia, item['Turno'], s_split_ini, s_split_fim)
                                    self.reservar(rec, dia2, item['Turno'], s_split_ini, s_split_fim)
                                    
                                    self.grade.append({
                                        "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "CH_Total": ch_total,
                                        "Dia": f"{dia} e {dia2}", "Turno": item['Turno'], "Docentes": item['Docentes'],
                                        "Espacos": f"{esp} ({sala})", "Semana_Inicio": s_split_ini, "Semana_Fim": s_split_fim,
                                        "Status": "✅ Alocado (Split)", "Obs": "Dividido em 2 dias"
                                    })
                                    sucesso = True
                                    break

                    if sucesso: break
                if sucesso: break
            
            if not sucesso:
                self.erros.append(f"❌ {item['ID_Turma']} - {item['Nome_UC']}")
                self.grade.append({"ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "Status": "❌ Erro"})
            
            bar.progress((idx + 1) / total)

        return pd.DataFrame(self.grade), self.erros

# --- INTERFACE ---
st.sidebar.header("📂 Área de Trabalho")
st.sidebar.download_button("📥 Baixar Modelo", gerar_template(), "modelo.xlsx")
st.sidebar.markdown("---")
up = st.sidebar.file_uploader("Upload Planilha", type=['xlsx'])

if up and st.button("🚀 Rodar Otimizador V14"):
    try:
        df_dem = pd.read_excel(up, sheet_name='Demandas')
        try: df_doc = pd.read_excel(up, sheet_name='Docentes')
        except: df_doc = pd.DataFrame()
        
        motor = MotorAlocacao(df_dem, df_doc)
        df_res, erros = motor.executar()
        
        st.success("Alocação Finalizada!")
        
        # ZIP
        buf = BytesIO()
        with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED, False) as z:
            z.writestr("01_Grade_Geral.csv", converter_csv(df_res))
            z.writestr("02_Erros.csv", converter_csv(pd.DataFrame(erros, columns=["Erro"])))
            
            if not df_res.empty:
                # Docentes Explodidos
                rows = []
                for _, row in df_res[df_res['Status'].str.contains("Alocado", na=False)].iterrows():
                    for d in str(row['Docentes']).split(','):
                        rows.append(row.to_dict() | {"Docente_Individual": d.strip()})
                z.writestr("04_Agenda_Docentes.csv", converter_csv(pd.DataFrame(rows)))
                
                # Espaços
                z.writestr("03_Ocupacao_Espacos.csv", converter_csv(df_res[['Dia', 'Turno', 'Espacos', 'ID_Turma', 'Semana_Inicio', 'Semana_Fim']]))

        st.download_button("📦 Baixar Resultados (ZIP)", buf.getvalue(), "Resultados_V14.zip", "application/zip")
        st.dataframe(df_res)
        
    except Exception as e:
        st.error(f"Erro: {e}")

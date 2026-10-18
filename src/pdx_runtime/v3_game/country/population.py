"""Targeted native State population reads; no all-state scan or event projection."""
from __future__ import annotations

from pdx_runtime import v3_data as data, v3_objects as objects

# Exact original State SOL tooltip mapping, not native enum ordinals.
STRATA_GETTERS=(("lower","GetNumberOfPopsPoor"),("middle","GetNumberOfPopsMiddle"),
               ("upper","GetNumberOfPopsRich"))


def _empty(country_id,state_id):
    strata={name:{"population":None,"radicals":None,"loyalists":None,
        "units":"people","reasons":{
            "radicals":"State totals do not identify radicals in this stratum",
            "loyalists":"State totals do not identify loyalists in this stratum"}}
        for name,_ in STRATA_GETTERS}
    return {"country_id":country_id,"state_id":state_id,"state_name":None,
        "population":None,"radicals":None,"loyalists":None,"strata":strata,
        "units":{"population":"people","radicals":"people","loyalists":"people"},
        "reasons":{},"snapshot":"separate native observations"}


async def read_state(api,read,country_id,state_id,*,details=False):
    ctx=api._ctx;answer=_empty(country_id,state_id)
    observed=await read.query("country.population.state",lambda:objects.read_ids(ctx.native,
        objects.Domain.STATE,(state_id,),adapter=ctx.adapters["objects"],process=ctx.process))
    if observed.error:
        answer["reasons"]["state"]="Native state lookup failed: "+str(observed.error)
        return answer
    if observed.world!=read.world:
        raise RuntimeError("State population lookup crossed a world change")
    if len(observed.records)!=1 or observed.records[0].error or observed.records[0].resolved_id!=state_id:
        answer["reasons"]["state"]="The state is not available in the current world"
        return answer
    if observed.records[0].owner_id!=country_id:
        answer["reasons"]["state"]="This state is not owned by the selected country"
        return answer
    binding=data.ObjectBinding.state(await ctx.type_id("State"),state_id)
    answer["state_name"],name_reason=await api._name(read,"State",binding)
    if name_reason:answer["reasons"]["state_name"]=name_reason
    for field,getter in (("population","GetPopulationSize"),("radicals","GetNumRadicals"),
                         ("loyalists","GetNumLoyalists")):
        answer[field],reason=await api._scalar(read,binding,getter,"int64",receiver="State")
        if reason:answer["reasons"][field]=reason
    for stratum,getter in STRATA_GETTERS:
        row=answer["strata"][stratum]
        row["population"],reason=await api._scalar(read,binding,getter,"int64",receiver="State")
        if reason:row["reasons"]["population"]=reason
    if details:
        await _read_pop_politics(api,read,binding,answer)
    return answer


def _copied_text(result):
    if (result.error or result.kind!=data.Kind.STRING or result.snapshot_bytes!=result.total
            or len(result.snapshot)!=result.snapshot_bytes):
        return None,"Native social-class identity text is incomplete"
    try:return result.snapshot.decode("utf-8","strict"),None
    except UnicodeDecodeError:return None,"Native social-class identity text is not UTF-8"


async def _chain_text(api,read,binding,chain,final_type):
    result,reason=await api._getter(read,"Pop",binding,"GetSocialClass",chain=chain,
        return_type_id=final_type)
    return (None,reason) if result is None else _copied_text(result)


async def _hierarchy_membership(api,read,binding,functions,tag_type_id):
    """Compare original opaque class identities from explicit strata sources.

    Each possibly borrowed SocialClass/Hierarchy is created and consumed inside
    one original evaluate_element callback. No definition pointer escapes.
    """
    ctx=api._ctx;members={};warnings=[]
    for tier,getter in (("lower","GetLowerStrataSocialClasses"),("middle","GetMiddleStrataSocialClasses"),
                        ("upper","GetUpperStrataSocialClasses")):
        function=functions.get(getter)
        if function is None:
            warnings.append("Native hierarchy source missing: "+getter);continue
        source="Pop.GetSocialClass.GetSocialHierarchy."+getter
        first=await read.query("population.class_membership."+tier,lambda source=source:data.evaluate_element(
            ctx.native,source,0,"SocialClass.GetTooltipTag",(binding,),world=read.world,
            adapter=ctx.adapters["data"],process=ctx.process))
        count=first.source_count
        if first.error:
            # Empty source and pre-production failure can share ERROR_NOT_FOUND.
            # Do not turn that ambiguous receipt into authoritative absence.
            warnings.append("Native "+tier+" class list unavailable: "+str(first.error));continue
        if not count:
            warnings.append("Native "+tier+" class count unavailable");continue
        stable=True;copied=[]
        for index in range(count):
            current=first if not index else await read.query("population.class_membership."+tier,
                lambda index=index,source=source,count=count:data.evaluate_element(ctx.native,source,index,
                    "SocialClass.GetTooltipTag",(binding,),world=read.world,expected_count=count,
                    adapter=ctx.adapters["data"],process=ctx.process))
            if current.error or current.source_count!=count:
                stable=False;warnings.append("The native "+tier+" class list changed during observation");break
            if current.action.result.type_id!=tag_type_id:
                stable=False;warnings.append("Native social-class tag type differs from current metadata");break
            tag,reason=_copied_text(current.action.result)
            if reason or not tag:
                stable=False;warnings.append(reason or "Native social class identity is empty");break
            copied.append(tag)
        if stable:
            for tag in copied:members.setdefault(tag,set()).add(tier)
    return members,warnings


async def _read_pop_politics(api,read,state_binding,answer):
    ctx=api._ctx
    scan={"initial_pop_records":None,"observed_pop_records":0,"unique_pop_records":0,
        "complete":False,"reasons":[],"snapshot":"multiple callbacks, not an atomic population snapshot"}
    answer["population_scan"]=scan
    try:
        pop_type=await ctx.type_id("Pop")
        class_type=await ctx.type_id("SocialClass")
        hierarchy_type=await ctx.type_id("SocialHierarchy")
    except ValueError as exc:
        scan["reasons"].append(str(exc));return
    required={}
    for receiver,type_id,getter in (("Pop",pop_type,"GetSocialClass"),("Pop",pop_type,"GetTooltipTag"),
            ("Pop",pop_type,"IsValid"),
            ("SocialClass",class_type,"GetTooltipTag"),("SocialClass",class_type,"GetSocialHierarchy"),
            ("SocialHierarchy",hierarchy_type,"GetTooltipTag"),
            ("SocialHierarchy",hierarchy_type,"GetLowerStrataSocialClasses"),
            ("SocialHierarchy",hierarchy_type,"GetMiddleStrataSocialClasses"),
            ("SocialHierarchy",hierarchy_type,"GetUpperStrataSocialClasses")):
        function,reason=await api._function(read,receiver,type_id,getter)
        required[(receiver,getter)]=function
        if function is None:scan["reasons"].append(reason)
    if any(required[(receiver,getter)] is None for receiver,getter in (
            ("Pop","GetSocialClass"),("Pop","GetTooltipTag"),("Pop","IsValid"),("SocialClass","GetTooltipTag"),
            ("SocialClass","GetSocialHierarchy"),("SocialHierarchy","GetTooltipTag"))):return
    source,reason=await api._getter(read,"State",state_binding,"GetPops")
    if source is None:scan["reasons"].append(reason);return
    count=await read.query("population.pop_count",lambda:data.list_count(ctx.native,source.value_handle,
        adapter=ctx.adapters["data"],process=ctx.process))
    if count.error:scan["reasons"].append("Native Pop count unavailable: "+str(count.error));return
    scan["initial_pop_records"]=count.total
    sums={tier:{"population":0,"radicals":0,"loyalists":0} for tier,_ in STRATA_GETTERS}
    usable={tier:{"population":True,"radicals":True,"loyalists":True} for tier,_ in STRATA_GETTERS}
    hierarchy_cache={};seen=set();whole=True
    for index in range(count.total):
        await read.check_world()
        popread=type(read)(api)
        # Only copied metadata is shared. Native values/row owners stay separate.
        if hasattr(read,"functions"):popread.functions=read.functions
        try:
            await popread.check_world()
            element=await popread.query("population.pop_element",lambda index=index:data.list_element(
                ctx.native,source.value_handle,index,adapter=ctx.adapters["data"],process=ctx.process))
            scan["observed_pop_records"]+=1
            if (element.error or not element.value_handle or "CPdxPtr" not in element.native_type_name
                    or "CDataPop" not in element.native_type_name):
                whole=False;scan["reasons"].append("Native Pop source did not provide an owning receiver for this observation");continue
            binding=data.ObjectBinding(pop_type,element.value_handle,retained=True)
            valid,reason=await api._getter(popread,"Pop",binding,"IsValid")
            if valid is None or valid.kind!=data.Kind.BOOL or not valid.value:
                whole=False;scan["reasons"].append(reason or "Native Pop is no longer valid");continue
            identity_result,reason=await api._getter(popread,"Pop",binding,"GetTooltipTag")
            tag,tag_reason=(None,reason) if identity_result is None else _copied_text(identity_result)
            if tag_reason or not tag or tag in seen:
                whole=False;scan["reasons"].append(tag_reason or "Native Pop identity repeated or missing");continue
            seen.add(tag);scan["unique_pop_records"]=len(seen)
            class_tag,reason=await _chain_text(api,popread,binding,".GetTooltipTag",
                required[("SocialClass","GetTooltipTag")].return_type_id)
            hierarchy_tag,hierarchy_reason=await _chain_text(api,popread,binding,".GetSocialHierarchy.GetTooltipTag",
                required[("SocialHierarchy","GetTooltipTag")].return_type_id)
            if reason or hierarchy_reason or not class_tag or not hierarchy_tag:
                whole=False;scan["reasons"].append(reason or hierarchy_reason or "Native social-class identity unavailable");continue
            if hierarchy_tag not in hierarchy_cache:
                hierarchy_cache[hierarchy_tag]=await _hierarchy_membership(api,popread,binding,
                    {getter:required[("SocialHierarchy",getter)] for getter in (
                        "GetLowerStrataSocialClasses","GetMiddleStrataSocialClasses","GetUpperStrataSocialClasses")},
                    required[("SocialClass","GetTooltipTag")].return_type_id)
            membership,warnings=hierarchy_cache[hierarchy_tag]
            matches=membership.get(class_tag,set())
            if len(matches)!=1:
                whole=False;scan["reasons"].append("Native social class does not identify exactly one observed stratum")
                scan["reasons"].extend(warnings);continue
            tier=next(iter(matches))
            for field,getter in (("population","GetTotalSize"),("radicals","GetNumRadicals"),("loyalists","GetNumLoyalists")):
                value,reason=await api._scalar(popread,binding,getter,"int64",receiver="Pop")
                if value is None:
                    usable[tier][field]=False
                    scan["reasons"].append(reason)
                else:sums[tier][field]+=value
        except BaseException:
            # The exact pending row may still borrow the original State source.
            # Retain that original graph too; never replace the unknown query.
            read.uncertain=True
            raise
        finally:
            try:
                await popread.close()
            except BaseException:
                read.uncertain=True
                raise
            if popread.uncertain:
                read.uncertain=True
    final_count=await read.query("population.pop_count_after",lambda:data.list_count(ctx.native,source.value_handle,
        adapter=ctx.adapters["data"],process=ctx.process))
    if final_count.error or final_count.total!=count.total:
        whole=False;scan["reasons"].append("The native Pop collection changed during observation")
    scan["complete"]=whole and all(all(fields.values()) for fields in usable.values())
    scan["reasons"]=list(dict.fromkeys(reason for reason in scan["reasons"] if reason))
    for tier,_ in STRATA_GETTERS:
        row=answer["strata"][tier]
        row["observed_population"]=sums[tier]["population"]
        for field in ("radicals","loyalists"):
            row["observed_"+field]=sums[tier][field]
            if whole and usable[tier][field]:
                row[field]=sums[tier][field];row["reasons"].pop(field,None)
            else:row["reasons"][field]="The targeted native Pop scan is incomplete for this stratum"

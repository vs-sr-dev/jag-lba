/* jag_anim.c - the fork's animation calls (LIB_3D/P_ANIM.C), with the state
 * in the actor.
 *
 * LBA1 keeps each body's interpolation state inside the body buffer, and its
 * P_ANIM functions take the body. On the Jaguar the bodies are JBD (packed
 * at build time, S43) and the state lives in T_OBJET (AnimSrcFrame,
 * AnimMemoTicks, PtrPoseSrc), which is what the GPU body pipeline reads. So
 * OBJECT.C's handful of calls come here instead, with the actor - fork edits
 * in JAGUAR_PORT.md, section 4:
 *
 *   SetInterDepObjet(frame, anim, body)  -> Jag_SetInterDepObjet(obj)
 *   SetAnimObjet(0, anim, body)          -> Jag_AnimReset(obj)
 *   StockInterAnim(buf, body)            -> Jag_AnimSnap(numobj)
 *   CopyInterAnim(oldbody, newbody)      -> nothing: the state is the actor's
 *
 * The arithmetic is body_draw.c's (Body_InterDep), verified on the bench and
 * on hardware since S42; this file only moves results into the fork's
 * globals.
 */
#include "hal.h"

/* P_ANIM.C's outputs, read by DoAnim right after the call. */
extern WORD AnimMasterRot;
extern WORD AnimStepAlpha, AnimStepBeta, AnimStepGamma;
extern WORD AnimStepX, AnimStepY, AnimStepZ;

/* JBD +4: nb_groups, big-endian. 0 = a rigid body, what LBA1 flagged with
 * the absence of INFO_ANIM (tools/body_digest.py). */
static int body_is_rigid(const T_OBJET *o)
{
    const UBYTE *b;

    if (o->Body < 0 || o->Body >= NbBodys)
        return 1;
    b = PtrBody[o->Body];
    if (!b)
        return 1;
    return ((b[4] << 8) | b[5]) == 0;
}

LONG Jag_SetInterDepObjet(T_OBJET *o)
{
    T_ANIMSTEP st;
    int r;

    /* P_ANIM.C:618: a body without groups is not animated, and the original
     * returns WITHOUT touching the step globals - DoAnim then uses whatever
     * the previous actor left there. Reproduced as is. */
    if (body_is_rigid(o))
        return 0;

    r = Body_InterDep(o, &st);
    if (r < 0) {
        /* No usable animation (missing entry, frame past the end). The fork
         * would have read garbage here; a still frame is the kind answer. */
        AnimMasterRot = 0;
        AnimStepAlpha = AnimStepBeta = AnimStepGamma = 0;
        AnimStepX = AnimStepY = AnimStepZ = 0;
        return 0;
    }
    AnimMasterRot = st.msrot;
    AnimStepBeta  = st.beta;
    AnimStepX     = st.x;
    AnimStepY     = st.y;
    AnimStepZ     = st.z;
    /* Alpha and gamma steps: set by the original, read by nobody in DoAnim. */
    AnimStepAlpha = 0;
    AnimStepGamma = 0;
    return r;
}

/* InitAnim on an actor with no animation yet: the new one starts from its
 * frame 0 (object_move.c's transcription, verified in S42). */
void Jag_AnimReset(T_OBJET *o)
{
    o->AnimSrcFrame = 0;
    o->PtrPoseSrc = 0;
    o->AnimMemoTicks = TimerRef;
}

/* InitAnim over a running animation: the blend starts from a PHOTOGRAPH of
 * the pose being held, not from a frame (StockInterAnim). Body_SnapPose takes
 * it; a photograph of a photograph is the rule (S42, TheEnd 0108). */
void Jag_AnimSnap(WORD numobj)
{
    T_OBJET *o = &ListObjet[numobj];

    Body_SnapPose(numobj);
    o->AnimSrcFrame = o->PtrPoseSrc ? -1 : 0;
    o->AnimMemoTicks = TimerRef;
}
